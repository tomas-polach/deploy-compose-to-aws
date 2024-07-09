import asyncio
import json
import subprocess
import shutil
import os
import string
from typing import Callable
from pathlib import Path
from datetime import datetime
from pprint import pprint as pp
import yaml
import boto3
from slugify import slugify
from ecs_composex.ecs_composex import generate_full_template
from ecs_composex.common.settings import ComposeXSettings
from ecs_composex.common.stacks import process_stacks
from src.utils.cloudformation_deployer import CloudFormationDeployer
from src.utils.logger import get_logger
from src.utils.to_pascal_case import to_pascal_case
from src.utils.generate_random_id import generate_random_id


logger = get_logger(__name__)


DEFAULT_IMAGE_URI_FORMAT = "{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com/{stack_name}/{service_name}:{git_commit}"
DEFAULT_ENVIRONMENT = "dev"
DEFAULT_TEMP_DIR = "_deployment_tmp"
DEFAULT_ECS_COMPOSEX_OUTPUT_DIR = f"{DEFAULT_TEMP_DIR}/cf_output"


class Deployment:
    def __init__(self,
        cf_stack_prefix: str,
        aws_region: str,
        env_name: str = DEFAULT_ENVIRONMENT,
        git_branch: str | None = None,
        git_commit: str | None = None,
        docker_compose_path: str = "docker-compose.yaml",
        ecs_compose_x_path: str = "ecs-compose-x.yaml",
        ecs_compose_x_substitutes: dict = {},
        ecr_keep_last_n_images: int | None = 10,
        mutable_tags: bool = True,
        image_uri_format: str = DEFAULT_IMAGE_URI_FORMAT,
        temp_dir: str | None = DEFAULT_TEMP_DIR,
    ):
        print('REGION:', aws_region)

        self.project_name = slugify(cf_stack_prefix)
        self.env_name = slugify(env_name)
        self.aws_region = aws_region
        self.docker_compose_path = Path(docker_compose_path)
        self.ecs_compose_orig_path = Path(ecs_compose_x_path)
        self.ecs_compose_x_substitutes = ecs_compose_x_substitutes
        self.ecr_keep_last_n_images = ecr_keep_last_n_images
        self.mutable_tags = mutable_tags
        self.image_uri_format = image_uri_format

        # compose internal params
        self.stack_name = f"{self.project_name}-{self.env_name}"
        self.ci_stack_name = f"{self.project_name}-{self.env_name}-ci"
        self.ci_s3_bucket_name = f"{self.project_name}-{self.env_name}-ci"

        if git_branch is not None and git_commit is not None:
            self.git_branch = git_branch
            self.git_commit = git_commit[:8]
        else:
            self.git_branch, self.git_commit = Deployment._git_get_branch_and_hash()
        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_") + generate_random_id(length=6)

        self.ci_s3_key_prefix = f'{self.stack_name}/{ts_str}'
        self.temp_dir = Path(temp_dir) / ts_str
        self.cf_main_dir = Path(self.temp_dir) / 'cf_main'
        self.cf_main_dir.mkdir(exist_ok=True, parents=True)
        self.cf_main_output_path = self.cf_main_dir / 'outputs.json'

        self.ecs_compose_path = Path(self.cf_main_dir) / self.ecs_compose_orig_path.name
        # create a working copy of the ecs-compose-x.yaml file for subsequent modifications
        shutil.copy(self.ecs_compose_orig_path, self.ecs_compose_path)

        self.docker_compose_override_path = Path(self.temp_dir) / f"docker-compose.override.yaml"

        self.aws_account_id = Deployment._aws_get_account_id()
        self.ecs_client = boto3.client("ecs", region_name=self.aws_region)
        self.s3_client = boto3.client("s3", region_name=self.aws_region)
        self.cfd = CloudFormationDeployer(region_name=self.aws_region)

    @staticmethod
    def _aws_get_account_id() -> str:
        cmd = "aws sts get-caller-identity --query Account --output text"
        result = Deployment._cmd_run(cmd)
        return result.strip()

    @staticmethod
    def _git_get_branch_and_hash() -> tuple[str, str] | tuple[None, None]:
        try:
            branch_name = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).strip().decode('utf-8')
            commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).strip().decode('utf-8')
            return branch_name, commit_hash
        except subprocess.CalledProcessError as e:
            logger.warning(f"An error occurred while running git commands: {e}")
            return None, None

    @staticmethod
    async def _cmd_run_async(cmd: str, input: bytes | None = None):
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE if input else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        if input is not None:
            stdout, stderr = await process.communicate(input=input)
        else:
            stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
        return stdout.decode()

    @staticmethod
    def _cmd_run(cmd: str, input: bytes | None = None):
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.PIPE if input else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if input is not None:
            stdout, stderr = process.communicate(input=input)
        else:
            stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
        return stdout.decode()

    async def _docker_login_ecr(self):
        cmd = f"aws ecr get-login-password --region {self.aws_region}"
        password = await Deployment._cmd_run_async(cmd)
        cmd = f"docker login --username AWS --password-stdin {self.aws_account_id}.dkr.ecr.{self.aws_region}.amazonaws.com"
        await Deployment._cmd_run_async(cmd, input=password.encode())

    def _cf_ci_generate(self, image_uris: list[str], ecr_keep_last_n_images: int | None = 10) -> dict[str, dict]:
        cf_template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {
                # Create bucket for deployment artifacts
                'DeploymentBucket': {
                    "Type": "AWS::S3::Bucket",
                    "Properties": {
                        "BucketName": self.ci_s3_bucket_name,
                        "VersioningConfiguration": {
                            "Status": "Enabled"
                        }
                    }
                }
            }
        }

        # create ECR repositories
        unique_repo_names = list(set(map(self._docker_get_repo_name_from_uri, image_uris)))
        for repo_name in unique_repo_names:
            resource_name = to_pascal_case(f'{repo_name}-repository')
            cf_template['Resources'][resource_name] = {
                "Type": "AWS::ECR::Repository",
                "Properties": {
                    "RepositoryName": repo_name,
                    # todo: replace this with registry level scan filters as this prop has been deprecated
                    "ImageScanningConfiguration": {"scanOnPush": True},
                    "ImageTagMutability": "MUTABLE" if self.mutable_tags else "IMMUTABLE",
                }
            }

            if ecr_keep_last_n_images is not None:
                # create ECR with policy retaining max N images
                cf_template['Resources'][resource_name]['Properties']["LifecyclePolicy"] = {
                    "LifecyclePolicyText": json.dumps({
                        "rules": [
                            {
                                "rulePriority": 1,
                                "description": f"Keep last {ecr_keep_last_n_images} images",
                                "selection": {
                                    "tagStatus": "any",
                                    "countType": "imageCountMoreThan",
                                    "countNumber": ecr_keep_last_n_images
                                },
                                "action": {
                                    "type": "expire"
                                }
                            }
                        ]
                    })
                }

        return cf_template

    def _cf_ci_deploy(self, cf_template: dict[str, dict]):
        self.cfd.create_or_update_stack(
            stack_name=self.ci_stack_name,
            template_body=yaml.dump(cf_template),
        )
        self.cfd.wait_for_stack_completion(stack_name=self.ci_stack_name)

    def _docker_get_image_uris_by_service_name(self) -> dict[str, str]:
        with self.docker_compose_path.open('r') as fd:
            docker_compose = yaml.safe_load(fd.read())

        # Compose docker image URIs for private builds
        all_services = docker_compose.get('services', {})
        services_with_build = {
            service_name: service_params
            for service_name, service_params in all_services.items()
            if 'build' in service_params
        }

        image_uri_by_service_name = {
            service_name: self.image_uri_format.format(
                aws_account_id=self.aws_account_id,
                aws_region=self.aws_region,
                project_name=self.project_name,
                env_name=self.env_name,
                stack_name=self.stack_name,
                service_name=service_name,
                git_branch=self.git_branch,
                git_commit=self.git_commit,
            ) for service_name in services_with_build.keys()
        }

        return image_uri_by_service_name

    @staticmethod
    def _docker_get_repo_name_from_uri(image_uri: str):
        return image_uri[image_uri.find('/') + 1:].split(':')[0]

    def _docker_generate_override_file(self, image_uri_by_service_name: dict[str, str]):
        override_config = {
            'services': {
                service_name: {
                    'image': image_uri
                } for service_name, image_uri in image_uri_by_service_name.items()
            }
        }
        with self.docker_compose_override_path.open('w') as fd:
            yaml.dump(override_config, fd)

    async def _docker_build_tag_push(self, image_uris: list[str]):
        # Build and tag images
        logger.debug(f"Building and tagging docker images ...")
        build_cmd = f'''COMPOSE_DOCKER_CLI_BUILD=1 \
DOCKER_BUILDKIT=1 \
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
docker-compose \
-p "{self.stack_name}" \
-f "{str(self.docker_compose_path)}" \
-f "{str(self.docker_compose_override_path)}" \
build --parallel'''
        await Deployment._cmd_run_async(build_cmd)

        # Push images
        logger.debug(f"Pushing docker images ...")
        await asyncio.gather(*[
            Deployment._cmd_run_async(f"docker push {image_uri}")
            for image_uri in image_uris
        ])

    def _cf_handle_placeholders(self):
        with self.ecs_compose_orig_path.open('r') as f:
            text = f.read()
        text = string.Template(text).substitute(self.ecs_compose_x_substitutes)
        with self.ecs_compose_path.open('w') as f:
            f.write(text)

    def _cf_generate(self):
        logger.debug(f"Generating CloudFormation template from Docker Compose ...")
        ecx_settings = ComposeXSettings(
            command="render",
            TemplateFormat="yaml",
            RegionName=self.aws_region,
            BucketName=self.ci_s3_bucket_name,
            Name=self.stack_name,
            DockerComposeXFile=[
                self.docker_compose_path,
                self.docker_compose_override_path,
                self.ecs_compose_path,
            ],
            OutputDirectory=str(self.cf_main_dir),
        )
        ecx_root_stack = generate_full_template(ecx_settings)
        process_stacks(ecx_root_stack, ecx_settings)

    def _cf_update(self, template_modifier: Callable[[dict[str, dict]], dict]):
        cf_template_by_filename = {}
        for cf_template_path in self.cf_main_dir.glob('*.yaml'):
            with cf_template_path.open('r') as fd:
                cf_template_by_filename[cf_template_path.name] = yaml.safe_load(fd.read())

        # apply template modifier
        cf_template_by_filename = template_modifier(cf_template_by_filename)

        for filename, cf_template in cf_template_by_filename.items():
            cf_template_path = self.cf_main_dir / filename
            with cf_template_path.open('w') as fd:
                fd.write(yaml.dump(cf_template))

    def _cf_update_template_urls(self, cf_template_by_filename: dict[str, dict]) -> dict[str, dict]:
        for cf_filename, cf_template in cf_template_by_filename.items():
            # for all resources
            if 'Resources' in cf_template:
                for r_name, r_params in cf_template["Resources"].items():
                    # update TemplateURLs
                    if r_params.get("Type") == "AWS::CloudFormation::Stack" and "TemplateURL" in r_params["Properties"]:
                        # get filename of current TemplateURL
                        filename = r_params["Properties"]["TemplateURL"].split('/')[-1]
                        # set TemplateURL to S3 target
                        r_params["Properties"]["TemplateURL"] = self._cf_get_template_url(
                            dir_path=self.cf_main_dir,
                            filename=filename,
                        )
        return cf_template_by_filename

    def _cf_upload_to_s3(self, dir_path: Path):
        # upload generated cf templates to S3
        for file_path in dir_path.glob('*'):
            if file_path.suffix in ['.yaml', '.yml', '.json']:
                with open(file_path, 'rb') as file:
                    s3_key = f'{self.ci_s3_key_prefix}/{dir_path.name}/{file_path.name}'
                    self.s3_client.upload_fileobj(file, self.ci_s3_bucket_name, s3_key)
                    logger.debug(f'Uploaded "{s3_key}" to S3 bucket "{self.ci_s3_bucket_name}')

    def _cf_get_template_url(self, dir_path: Path, filename: str):
        return f"https://{self.ci_s3_bucket_name}.s3.{self.aws_region}.amazonaws.com/{self.ci_s3_key_prefix}/{dir_path.name}/{filename}"

    def _cf_deploy(self):
        # if stack doesn't exist, set ECS defaults
        if not self.cfd.stack_exists(self.stack_name):
            # https://github.com/compose-x/ecs_composex/blob/ff97d079113de5b1660c1beeafb24c8610971d10/ecs_composex/utils/init_ecs.py#L11
            for setting in [
                "awsvpcTrunking",
                "serviceLongArnFormat",
                "taskLongArnFormat",
                "containerInstanceLongArnFormat",
                "containerInsights",
            ]:
                self.ecs_client.put_account_setting_default(name=setting, value="enabled")
                logger.info(f"ECS Setting {setting} set to 'enabled'")

        # todo: check if stack exists and is in ROLLBACK_COMPLETE state --> delete the stack and re-create
        self.cfd.create_or_update_stack(
            stack_name=self.stack_name,
            template_url=self._cf_get_template_url(
                dir_path=self.cf_main_dir,
                filename=f"{self.stack_name}.yaml"
            ),
        )
        self.cfd.wait_for_stack_completion(self.stack_name)

    async def run(
            self,
            keep_temp_files: bool = False
    ):
        # compile future docker image URIs for locally built docker images
        docker_image_uri_by_service_name = self._docker_get_image_uris_by_service_name()

        # CloudFormation: ci stack (ECR repos for locally built docker images and ci bucket)
        # note: ci cf template can't be uploaded to S3 because the ci bucket will be created in the ci stack
        cf_ci_template = self._cf_ci_generate(
            image_uris=list(docker_image_uri_by_service_name.values()),
            ecr_keep_last_n_images=self.ecr_keep_last_n_images,
        )
        self._cf_ci_deploy(cf_ci_template)

        # Docker:
        # generate docker-compose.override.yaml which will add docker image URIs to services with local docker builds,
        # so that docker knows where to push the locally built images to
        self._docker_generate_override_file(docker_image_uri_by_service_name)
        await self._docker_login_ecr()
        await self._docker_build_tag_push(image_uris=list(docker_image_uri_by_service_name.values()))

        # CloudFormation: main stack
        self._cf_handle_placeholders()
        self._cf_generate()
        self._cf_update(template_modifier=self._cf_update_template_urls)
        return
        self._cf_upload_to_s3(dir_path=self.cf_main_dir)
        self._cf_deploy()

        cf_main_output = self.cfd.get_stack_outputs(self.stack_name)
        pp(cf_main_output)
        # Write outputs to a file
        with self.cf_main_output_path.open('w') as f:
            f.write(cf_main_output)

        # Optionally, set an output to indicate the file path
        with open(os.environ['GITHUB_OUTPUT'], 'a') as gh_output:
            gh_output.write(f'cf_outputs_path={self.cf_main_output_path}\n')

        # delete temp dir
        if keep_temp_files is not True:
            shutil.rmtree(self.temp_dir)

        # todo: keep only the last 10 versions of the ci stack on S3
