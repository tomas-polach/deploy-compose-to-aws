import asyncio
import json
import subprocess
import shutil
import os
import string
import base64
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
DEFAULT_IMAGE_CACHE_URI_FORMAT = "{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com/{stack_name}/{service_name}:buildcache"
DEFAULT_ENVIRONMENT = "dev"
DEFAULT_TEMP_DIR = "_deployment_tmp"
DEFAULT_ECS_COMPOSEX_OUTPUT_DIR = f"{DEFAULT_TEMP_DIR}/cf_output"


class Deployment:
    def __init__(
        self,
        cf_stack_prefix: str,
        aws_region: str,
        env_name: str = DEFAULT_ENVIRONMENT,
        git_branch: str | None = None,
        git_commit: str | None = None,
        docker_compose_path: str = "docker-compose.yaml",
        ecs_compose_x_path: str = "ecs-compose-x.yaml",
        ecs_compose_x_sub: dict = {},
        ecr_keep_last_n_images: int | None = 10,
        mutable_tags: bool = True,
        image_uri_format: str = DEFAULT_IMAGE_URI_FORMAT,
        temp_dir: str | None = DEFAULT_TEMP_DIR,
        keep_temp_files: bool = True,
    ):
        pp(ecs_compose_x_sub)

        self.project_name = slugify(cf_stack_prefix)
        self.env_name = slugify(env_name)
        self.aws_region = aws_region
        self.docker_compose_path = Path(docker_compose_path)
        self.ecs_compose_orig_path = Path(ecs_compose_x_path)
        self.ecs_compose_x_subs = ecs_compose_x_sub
        self.ecr_keep_last_n_images = ecr_keep_last_n_images
        self.mutable_tags = mutable_tags
        self.image_uri_format = image_uri_format
        self.image_cache_uri_format = DEFAULT_IMAGE_CACHE_URI_FORMAT

        # compose internal params
        self.stack_name = f"{self.project_name}-{self.env_name}"
        self.ci_stack_name = f"{self.project_name}-{self.env_name}-ci"
        self.ci_s3_bucket_name = f"{self.project_name}-{self.env_name}-ci"

        if git_branch is not None and git_commit is not None:
            self.git_branch = git_branch
            self.git_commit = git_commit[:8]
        else:
            self.git_branch, self.git_commit = Deployment._git_get_branch_and_hash()
        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_") + generate_random_id(
            length=6
        )

        self.ci_s3_key_prefix = f"{self.stack_name}/{ts_str}"
        self.keep_temp_files = keep_temp_files
        self.temp_dir = Path(temp_dir) / ts_str
        self.cf_main_dir = Path(self.temp_dir) / "cf_main"
        self.cf_main_dir.mkdir(exist_ok=True, parents=True)
        self.cf_main_output_path = self.cf_main_dir / "outputs.json"
        self.cf_disable_rollback = True

        self.ecs_compose_path = Path(self.temp_dir) / self.ecs_compose_orig_path.name

        self.docker_compose_override_path = (
            Path(self.temp_dir) / f"docker-compose.override.yaml"
        )

        self.aws_account_id = Deployment._aws_get_account_id()
        self.ecs_client = boto3.client("ecs", region_name=self.aws_region)
        self.s3_client = boto3.client("s3", region_name=self.aws_region)
        self.ecr_client = boto3.client("ecr", region_name=self.aws_region)
        self.cfd = CloudFormationDeployer(region_name=self.aws_region)

    async def run(self):
        # compile future docker image URIs for locally built docker images
        docker_image_uri_by_service_name = self._docker_get_image_uris_by_service_name()

        # CloudFormation: ci stack (ECR repos for locally built docker images and ci bucket)
        # note: ci cf template can't be uploaded to S3 because the ci bucket will be created in the ci stack
        unique_repo_names = list(
            set(
                map(
                    self._docker_get_repo_name_from_uri,
                    docker_image_uri_by_service_name.values(),
                )
            )
        )
        cf_ci_template = self._cf_ci_generate(
            unique_repo_names=unique_repo_names,
            ecr_keep_last_n_images=self.ecr_keep_last_n_images,
        )
        self._cf_ci_deploy(cf_ci_template)

        # Docker:
        # generate docker-compose.override.yaml which will add docker image URIs to services with local docker builds,
        # so that docker knows where to push the locally built images to
        self._docker_generate_override_file(docker_image_uri_by_service_name)
        await self._docker_login_ecr()
        await self._docker_build_tag_push(
            docker_image_uri_by_service_name=docker_image_uri_by_service_name
        )

        # CloudFormation: main stack
        self._cf_handle_placeholders()
        self._cf_generate()
        self._cf_update(template_modifier=self._cf_update_template_urls)
        self._cf_upload_to_s3(dir_path=self.cf_main_dir)
        self._cf_deploy()
        self._cf_store_outputs()

        # delete temp dir
        if self.keep_temp_files is not True:
            shutil.rmtree(self.temp_dir)

        # todo: keep only the last 10 versions of the ci stack on S3

    @staticmethod
    def _aws_get_account_id() -> str:
        cmd = "aws sts get-caller-identity --query Account --output text"
        result = Deployment._cmd_run(cmd)
        return result.strip()

    @staticmethod
    def _git_get_branch_and_hash() -> tuple[str, str] | tuple[None, None]:
        try:
            branch_name = (
                subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
                .strip()
                .decode("utf-8")
            )
            commit_hash = (
                subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
                .strip()
                .decode("utf-8")
            )
            return branch_name, commit_hash
        except subprocess.CalledProcessError as e:
            logger.warning(f"An error occurred while running git commands: {e}")
            return None, None

    @staticmethod
    async def _cmd_run_async(cmd: str, input: bytes | None = None) -> str:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE if input else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if input is not None:
            stdout, stderr = await process.communicate(input=input)
        else:
            stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
        return stdout.decode()

    @staticmethod
    def _cmd_run(cmd: str, input: bytes | None = None) -> str:
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdin=subprocess.PIPE if input else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if input is not None:
            stdout, stderr = process.communicate(input=input)
        else:
            stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise ValueError(f"Command failed: {cmd}\n{stderr.decode()}")
        return stdout.decode()

    async def _docker_login_ecr(self) -> None:
        # Get the ECR authorization token
        response = self.ecr_client.get_authorization_token()
        auth_data = response['authorizationData'][0]
        auth_token = auth_data['authorizationToken']
        registry_url = auth_data['proxyEndpoint']
        username, password = base64.b64decode(auth_token).decode('utf-8').split(':')
        # Login to the ECR registry
        cmd = f"docker login --username {username} --password-stdin {registry_url}"
        #subprocess.run(login_command, shell=True, check=True)
        await Deployment._cmd_run_async(cmd, input=password.encode())

    def _cf_ci_generate(
        self, unique_repo_names: list[str], ecr_keep_last_n_images: int | None = 10
    ) -> dict[str, dict]:
        cf_template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {
                # Create bucket for deployment artifacts
                "DeploymentBucket": {
                    "Type": "AWS::S3::Bucket",
                    "Properties": {
                        "BucketName": self.ci_s3_bucket_name,
                        "VersioningConfiguration": {"Status": "Enabled"},
                    },
                }
            },
        }

        # create ECR repositories
        for repo_name in unique_repo_names:
            resource_name = to_pascal_case(f"{repo_name}-repository")
            cf_template["Resources"][resource_name] = {
                "Type": "AWS::ECR::Repository",
                "Properties": {
                    "RepositoryName": repo_name,
                    # todo: replace this with registry level scan filters as this prop has been deprecated
                    "ImageScanningConfiguration": {"scanOnPush": True},
                    "ImageTagMutability": (
                        "MUTABLE" if self.mutable_tags else "IMMUTABLE"
                    ),
                },
            }

            if ecr_keep_last_n_images is not None:
                # create ECR with policy retaining max N images
                cf_template["Resources"][resource_name]["Properties"][
                    "LifecyclePolicy"
                ] = {
                    "LifecyclePolicyText": json.dumps(
                        {
                            "rules": [
                                {
                                    "rulePriority": 1,
                                    "description": f"Keep last {ecr_keep_last_n_images} images",
                                    "selection": {
                                        "tagStatus": "any",
                                        "countType": "imageCountMoreThan",
                                        "countNumber": ecr_keep_last_n_images,
                                    },
                                    "action": {"type": "expire"},
                                }
                            ]
                        }
                    )
                }

        return cf_template

    def _cf_ci_deploy(self, cf_template: dict[str, dict]) -> None:
        self.cfd.create_or_update_stack(
            stack_name=self.ci_stack_name,
            template_body=yaml.dump(cf_template),
        )
        self.cfd.wait_for_stack_completion(stack_name=self.ci_stack_name)

    def _docker_get_image_uris_by_service_name(self) -> dict[str, str]:
        with self.docker_compose_path.open("r") as fd:
            docker_compose = yaml.safe_load(fd.read())

        # Compose docker image URIs for private builds
        all_services = docker_compose.get("services", {})
        services_with_build = {
            service_name: service_params
            for service_name, service_params in all_services.items()
            if "build" in service_params
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
            )
            for service_name in services_with_build.keys()
        }

        return image_uri_by_service_name

    @staticmethod
    def _docker_get_repo_name_from_uri(image_uri: str) -> str:
        return image_uri.split(".amazonaws.com/")[-1].split(":")[0]

    def _docker_generate_override_file(
        self, image_uri_by_service_name: dict[str, str]
    ) -> None:
        override_config = {
            "services": {
                service_name: {"image": image_uri}
                for service_name, image_uri in image_uri_by_service_name.items()
            }
        }
        with self.docker_compose_override_path.open("w") as fd:
            yaml.dump(override_config, fd)

    async def _docker_build_tag_push(self, docker_image_uri_by_service_name: dict[str, str]) -> None:
        # Create a new Buildx builder instance and use it
        logger.debug(f"Setting up Docker Buildx ...")
        buildx_create_cmd = "docker buildx create --use"
        await Deployment._cmd_run_async(buildx_create_cmd)

        # Load Docker Compose configuration
        with open(self.docker_compose_path, 'r') as file:
            docker_compose = yaml.safe_load(file)

        services_with_build = {
            service_name: service_params
            for service_name, service_params in docker_compose.get("services", {}).items()
            if "build" in service_params
        }

        build_cmds = []
        image_uris_to_push = []
        for service_name, service_params in services_with_build.items():
            service_image_uri = docker_image_uri_by_service_name[service_name]
            image_uris_to_push.append(service_image_uri)

            # Handle platform if present
            platform = service_params.get('platform', 'linux/amd64')
            platform_str = f"--platform {platform}"

            # Determine the build context and Dockerfile path
            build_context = service_params['build']
            if isinstance(build_context, str):
                # e.g. build: ./my-dir
                service_build_context = build_context
                service_dockerfile = Path(build_context) / 'Dockerfile'

                build_args_str = ''
                build_target_str = ''
            elif isinstance(build_context, dict):
                # e.g. build: { context: ./my-dir, dockerfile: Dockerfile.dev, args: { key: value } }
                service_build_context = build_context.get('context', '.')
                service_dockerfile = Path(service_build_context) / build_context.get('dockerfile', 'Dockerfile')

                # Handle build args if present
                build_args = build_context.get('args', {})
                build_args_str = ' '.join([f"--build-arg {k}={v}" for k, v in build_args.items()])

                # Handle target if present
                build_target = build_context.get('target', None)
                build_target_str = f"--target {build_target}" if build_target else ''
            else:
                raise ValueError(f"Invalid build context for service {service_name}")

            image_cache_uri = self.image_cache_uri_format.format(
                aws_account_id=self.aws_account_id,
                aws_region=self.aws_region,
                project_name=self.project_name,
                env_name=self.env_name,
                stack_name=self.stack_name,
                service_name=service_name,
                git_branch=self.git_branch,
                git_commit=self.git_commit,
            )

            # Build and tag images with Buildx, using the cache from the registry and pushing the cache back to the registry
            logger.debug(f"Building and tagging docker images for service {service_name} with Buildx ...")
            build_cmd = f"""docker buildx build \
{platform_str} \
--cache-from=type=registry,ref={image_cache_uri} \
--cache-to=type=registry,ref={image_cache_uri},mode=max \
--file {service_dockerfile} \
{build_args_str} \
{build_target_str} \
--tag {service_image_uri} \
--push \
{service_build_context}"""
            build_cmds.append(build_cmd)

        logger.debug(f"Building and tagging docker images ...")
        await asyncio.gather(
            *[
                Deployment._cmd_run_async(build_cmd)
                for build_cmd in build_cmds
            ]
        )

        # Push images
        logger.debug(f"Pushing docker images ...")
        await asyncio.gather(
            *[
                Deployment._cmd_run_async(f"docker push {image_uri}")
                for image_uri in image_uris_to_push
            ]
        )

    def _cf_handle_placeholders(self):
        with self.ecs_compose_orig_path.open("r") as f:
            text = f.read()
        text = string.Template(text).substitute(self.ecs_compose_x_subs)
        with self.ecs_compose_path.open("w") as f:
            f.write(text)

    def _cf_generate(self) -> None:
        logger.debug(f"Generating CloudFormation template from Docker Compose ...")
        ecx_settings = ComposeXSettings(
            command="render",
            TemplateFormat="yaml",
            RegionName=self.aws_region,
            BucketName=self.ci_s3_bucket_name,
            Name=self.stack_name,
            disable_rollback=self.cf_disable_rollback,
            DockerComposeXFile=[
                self.docker_compose_path,
                self.docker_compose_override_path,
                self.ecs_compose_path,
            ],
            OutputDirectory=str(self.cf_main_dir),
        )
        ecx_root_stack = generate_full_template(ecx_settings)
        process_stacks(ecx_root_stack, ecx_settings)

    def _cf_update(self, template_modifier: Callable[[dict[str, dict]], dict]) -> None:
        cf_template_by_filename = {}
        for cf_template_path in self.cf_main_dir.glob("*.yaml"):
            with cf_template_path.open("r") as fd:
                cf_template_by_filename[cf_template_path.name] = yaml.safe_load(
                    fd.read()
                )

        # apply template modifier
        cf_template_by_filename = template_modifier(cf_template_by_filename)

        for filename, cf_template in cf_template_by_filename.items():
            cf_template_path = self.cf_main_dir / filename
            with cf_template_path.open("w") as fd:
                fd.write(yaml.dump(cf_template))

    def _cf_update_template_urls(
        self, cf_template_by_filename: dict[str, dict]
    ) -> dict[str, dict]:
        for cf_filename, cf_template in cf_template_by_filename.items():
            # for all resources
            if "Resources" in cf_template:
                for r_name, r_params in cf_template["Resources"].items():
                    # update TemplateURLs
                    if (
                        r_params.get("Type") == "AWS::CloudFormation::Stack"
                        and "TemplateURL" in r_params["Properties"]
                    ):
                        # get filename of current TemplateURL
                        filename = r_params["Properties"]["TemplateURL"].split("/")[-1]
                        # set TemplateURL to S3 target
                        r_params["Properties"]["TemplateURL"] = (
                            self._cf_get_template_url(
                                dir_path=self.cf_main_dir,
                                filename=filename,
                            )
                        )
        return cf_template_by_filename

    def _cf_upload_to_s3(self, dir_path: Path) -> None:
        # upload generated cf templates to S3
        for file_path in dir_path.glob("*"):
            if file_path.suffix in [".yaml", ".yml", ".json"]:
                with open(file_path, "rb") as file:
                    s3_key = f"{self.ci_s3_key_prefix}/{dir_path.name}/{file_path.name}"
                    self.s3_client.upload_fileobj(file, self.ci_s3_bucket_name, s3_key)
                    logger.debug(
                        f'Uploaded "{s3_key}" to S3 bucket "{self.ci_s3_bucket_name}'
                    )

    def _cf_get_template_url(self, dir_path: Path, filename: str):
        return f"https://{self.ci_s3_bucket_name}.s3.{self.aws_region}.amazonaws.com/{self.ci_s3_key_prefix}/{dir_path.name}/{filename}"

    def _cf_deploy(self) -> None:
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
                self.ecs_client.put_account_setting_default(
                    name=setting, value="enabled"
                )
                logger.info(f"ECS Setting {setting} set to 'enabled'")

        # todo: check if stack exists and is in ROLLBACK_COMPLETE state --> delete the stack and re-create
        self.cfd.create_or_update_stack(
            stack_name=self.stack_name,
            template_url=self._cf_get_template_url(
                dir_path=self.cf_main_dir, filename=f"{self.stack_name}.yaml"
            ),
        )
        self.cfd.wait_for_stack_completion(self.stack_name)

    def _cf_store_outputs(self) -> None:
        cf_main_output = self.cfd.get_nested_stack_outputs(self.stack_name)

        outputs_by_output_key = {
            o["OutputKey"]: o["OutputValue"] for o in cf_main_output if "OutputKey" in o
        }
        outputs_by_export_name = {
            o["ExportName"]: o["OutputValue"]
            for o in cf_main_output
            if "ExportName" in o
        }

        # Write outputs to a file
        with self.cf_main_output_path.open("w") as f:
            f.write(
                json.dumps(
                    {
                        "by_output_key": outputs_by_output_key,
                        "by_export_name": outputs_by_export_name,
                        "raw": cf_main_output,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )

        # Optionally, set an output to indicate the file path
        with open(os.environ["GITHUB_OUTPUT"], "a") as gh_output:
            gh_output.write(f"cf-output-path={self.cf_main_output_path.resolve()}\n")
