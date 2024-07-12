import asyncio
import json
import shutil
import os
import string
import base64
from typing import Callable
from pathlib import Path
from datetime import datetime
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
from src.utils.run_cmd import run_cmd_async
from src.utils.github_helper import git_get_branch_and_hash


logger = get_logger(__name__)


# !IMPORTANT: path MUST have 3 components (e.g. "/a/b/c") or docker push will fail with HTTP 400
DEFAULT_IMAGE_URI_FORMAT = "{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com/{cf_stack_prefix}/{env_name}/{service_name}:{git_commit}"
DEFAULT_ENVIRONMENT = "dev"
DEFAULT_TEMP_DIR = "_deployment_tmp"
DEFAULT_ECS_COMPOSEX_OUTPUT_DIR = f"{DEFAULT_TEMP_DIR}/cf_output"


class Deployment:
    def __init__(
        self,
        cf_stack_prefix: str,
        aws_region: str,
        env_name: str | None = None,
        git_branch: str | None = None,
        git_commit: str | None = None,
        docker_compose_path: str | None = None,
        ecs_composex_path: str | None = None,
        ecs_composex_subs: dict[str, str] | None = None,
        ecr_keep_last_n_images: int | None = 10,
        mutable_tags: bool | None = None,
        image_uri_format: str = DEFAULT_IMAGE_URI_FORMAT,
        temp_dir: str = DEFAULT_TEMP_DIR,
        keep_temp_files: bool = True,
    ):
        self.cf_stack_prefix = slugify(cf_stack_prefix)
        self.env_name = slugify(env_name or DEFAULT_ENVIRONMENT)
        self.aws_region = aws_region
        self.docker_compose_path = Path(docker_compose_path or "docker-compose.yaml")
        self.ecs_compose_orig_path = Path(ecs_composex_path or "ecs-composex.yaml")
        self.ecs_composex_subs = ecs_composex_subs or {}
        self.ecr_keep_last_n_images = ecr_keep_last_n_images
        self.mutable_tags = mutable_tags or False
        self.image_uri_format = image_uri_format

        # compose internal params
        self.stack_name = f"{self.cf_stack_prefix}-{self.env_name}"
        self.ci_stack_name = f"{self.cf_stack_prefix}-{self.env_name}-ci"
        self.ci_s3_bucket_name = f"{self.cf_stack_prefix}-{self.env_name}-ci"

        if git_branch is not None and git_commit is not None:
            self.git_branch = git_branch
            self.git_commit = git_commit[:8]
        else:
            self.git_branch, self.git_commit = git_get_branch_and_hash()

        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_") + generate_random_id(6)

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

        # set redundant env vars since some libraries use AWS_DEFAULT_REGION while others use AWS_REGION
        os.environ["AWS_REGION"] = aws_region
        os.environ["AWS_DEFAULT_REGION"] = aws_region

        self.ecs_client = boto3.client("ecs", region_name=self.aws_region)
        self.s3_client = boto3.client("s3", region_name=self.aws_region)
        self.ecr_client = boto3.client("ecr", region_name=self.aws_region)
        self.cfd = CloudFormationDeployer(region_name=self.aws_region)
        self.aws_account_id = self.cfd.get_account_id()

        print('REGION', self.aws_region)

    async def run(self):
        # compile future docker image URIs for locally built docker images
        docker_image_uri_by_service_name = self._docker_get_image_uris_by_service_name()

        # CloudFormation: ci stack (ECR repos for locally built docker images and ci bucket)
        # note: ci cf template can't be uploaded to S3 because the ci bucket will be created in the ci stack
        cf_ci_template = self._cf_ci_generate(docker_image_uri_by_service_name)
        self._cf_ci_deploy(cf_ci_template)

        # Docker:
        # generate docker-compose.override.yaml which will add docker image URIs to services with local docker builds,
        # so that docker knows where to push the locally built images to
        self._docker_generate_override_file(docker_image_uri_by_service_name)
        await self._docker_login_ecr()
        await self._docker_build_tag_push(docker_image_uri_by_service_name)

        # CloudFormation: main stack
        self._cf_handle_placeholders()
        self._cf_generate()
        self._cf_update(template_modifier=self._cf_update_template_urls)
        self._cf_upload_to_s3()
        self._cf_deploy()
        self._cf_store_outputs()

        # delete temp dir
        if self.keep_temp_files is not True:
            shutil.rmtree(self.temp_dir)

        # todo: keep only the last 10 versions of the ci stack on S3

    async def _docker_login_ecr(self) -> None:
        # Get the ECR authorization token
        response = self.ecr_client.get_authorization_token()
        auth_data = response["authorizationData"][0]
        auth_token = auth_data["authorizationToken"]
        registry_url = auth_data["proxyEndpoint"]
        username, password = base64.b64decode(auth_token).decode("utf-8").split(":")
        # Login to the ECR registry
        cmd = f"docker login --username {username} --password-stdin {registry_url}"
        await run_cmd_async(cmd, input=password.encode())

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
                cf_stack_prefix=self.cf_stack_prefix,
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

    async def _docker_build_tag_push(
        self, docker_image_uri_by_service_name: dict[str, str]
    ) -> None:
        # Create a new Buildx builder instance and use it
        logger.debug(f"Setting up Docker Buildx ...")
        buildx_create_cmd = "docker buildx create --use"
        await run_cmd_async(buildx_create_cmd)

        # Load Docker Compose configuration
        with open(self.docker_compose_path, "r") as file:
            docker_compose = yaml.safe_load(file)

        services_with_build = {
            service_name: service_params
            for service_name, service_params in docker_compose.get(
                "services", {}
            ).items()
            if "build" in service_params
        }

        # todo: deduplicate builds if a docker is used by multiple services (e.g. with various command line args)

        build_cmds = []
        for service_name, service_params in services_with_build.items():
            service_image_uri = docker_image_uri_by_service_name[service_name]

            # Handle platform if present
            platform = service_params.get("platform", "linux/amd64")
            platform_str = f"--platform {platform}"

            # Determine the build context and Dockerfile path
            build_context = service_params["build"]
            if isinstance(build_context, str):
                # e.g. build: ./my-dir
                service_build_context = build_context
                service_dockerfile = Path(build_context) / "Dockerfile"

                build_args_str = ""
                build_target_str = ""
            elif isinstance(build_context, dict):
                # e.g. build: { context: ./my-dir, dockerfile: Dockerfile.dev, args: { key: value } }
                service_build_context = build_context.get("context", ".")
                service_dockerfile = Path(service_build_context) / build_context.get(
                    "dockerfile", "Dockerfile"
                )

                # Handle build args if present
                build_args = build_context.get("args", {})
                build_args_str = " ".join(
                    [f"--build-arg {k}={v}" for k, v in build_args.items()]
                )

                # Handle target if present
                build_target = build_context.get("target", None)
                build_target_str = f"--target {build_target}" if build_target else ""

                # Handle cache_from if present
                cache_from = build_context.get("cache_from", 'type=local,src=/tmp/.buildx-cache')
                cache_from_str = f"--cache-from {cache_from}" if cache_from else ""
            else:
                raise ValueError(f"Invalid build context for service {service_name}")

            # Build, tag and push images with Buildx, using the cache from the local storage
            build_cmd = f"""docker buildx build \
{platform_str} \
{cache_from_str} \
--cache-to type=local,dest=/tmp/.buildx-cache,mode=max \
--file {service_dockerfile} \
{build_args_str} \
{build_target_str} \
--tag {service_image_uri} \
--push \
--quiet \
{service_build_context}"""
            logger.debug(
                f"Building and tagging docker images for service {service_name} with Buildx ...\n  {build_cmd}"
            )
            build_cmds.append(build_cmd)

        await asyncio.gather(
            *[run_cmd_async(build_cmd) for build_cmd in build_cmds]
        )

    def _cf_ci_generate(
        self, docker_image_uri_by_service_name: dict[str, str]
    ) -> dict[str, dict]:
        unique_repo_names = list(
            set(
                map(
                    self._docker_get_repo_name_from_uri,
                    docker_image_uri_by_service_name.values(),
                )
            )
        )

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

            if self.ecr_keep_last_n_images is not None:
                # create ECR with policy retaining max N images
                cf_template["Resources"][resource_name]["Properties"][
                    "LifecyclePolicy"
                ] = {
                    "LifecyclePolicyText": json.dumps(
                        {
                            "rules": [
                                {
                                    "rulePriority": 1,
                                    "description": f"Keep last {self.ecr_keep_last_n_images} images",
                                    "selection": {
                                        "tagStatus": "any",
                                        "countType": "imageCountMoreThan",
                                        "countNumber": self.ecr_keep_last_n_images,
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

    def _cf_handle_placeholders(self):
        with self.ecs_compose_orig_path.open("r") as f:
            text = f.read()
        text = string.Template(text).substitute(self.ecs_composex_subs)
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
        for cf_template in cf_template_by_filename.values():
            # for all resources
            if "Resources" in cf_template:
                for r_params in cf_template["Resources"].values():
                    # update TemplateURLs in nested stacks
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

    def _cf_upload_to_s3(self) -> None:
        # upload generated cf templates to S3
        for file_path in self.cf_main_dir.glob("*"):
            if file_path.suffix in [".yaml", ".yml", ".json"]:
                with open(file_path, "rb") as file:
                    s3_key = f"{self.ci_s3_key_prefix}/{self.cf_main_dir.name}/{file_path.name}"
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

        # Set an output to indicate the file path
        with open(os.environ["GITHUB_OUTPUT"], "a") as gh_output:
            gh_output.write(f"cf-output-path={self.cf_main_output_path.resolve()}\n")
