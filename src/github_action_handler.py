import os
import yaml
import asyncio
from src.deploy import Deployment


def getenv(var_name: str, default=None):
    # returns None also if the value is an empty string
    value = os.getenv(var_name, default)
    return value if value != "" else default


def github_action_handler():
    cf_stack_prefix = getenv("INPUT_CF_STACK_PREFIX", None)
    cf_template_path = getenv("INPUT_CF_TEMPLATE_PATH", None)
    cf_parameter_overrides = getenv("INPUT_CF_PARAMETER_OVERRIDES", None)
    build_params = getenv("INPUT_BUILD_PARAMS", None)
    env_name = getenv("INPUT_ENV_NAME", None)
    ecr_keep_last_n_images = getenv("INPUT_ECR_KEEP_LAST_N_IMAGES", None)

    aws_region = getenv("AWS_REGION", None) or getenv("AWS_DEFAULT_REGION", None)

    is_github_action = getenv("GITHUB_ACTIONS") == "true"
    github_workspace_dir = getenv("GITHUB_WORKSPACE", "/github/workspace")
    git_repo_name = getenv("GITHUB_REPOSITORY", None)
    git_ref = getenv("GITHUB_REF", None)
    git_commit = getenv("GITHUB_SHA", None)

    # check required env vars

    # check if aws region is set
    if aws_region is None:
        raise ValueError("AWS_REGION environment variable is not set")

    # process params

    # convert cf_parameter_overrides to dict
    if cf_parameter_overrides is not None:
        try:
            cf_parameter_overrides = yaml.safe_load(cf_parameter_overrides)
        except yaml.YAMLError as e:
            raise ValueError(
                f"Invalid value provided for CF_PARAMETER_OVERRIDES. {str(e)}"
            )

    # convert build_params to dict
    if build_params is not None:
        try:
            build_params = yaml.safe_load(build_params)
        except yaml.YAMLError as e:
            raise ValueError(
                f"Invalid value provided for BUILD_PARAMS. {str(e)}"
            )

    # # convert ecr_keep_last_n_images to int
    if ecr_keep_last_n_images == "" or ecr_keep_last_n_images == "0":
        ecr_keep_last_n_images = None
    elif ecr_keep_last_n_images is not None:
        try:
            ecr_keep_last_n_images = int(ecr_keep_last_n_images)
        except ValueError:
            raise ValueError(
                "Invalid value provided for ECR_KEEP_LAST_N_IMAGES. Must be an integer"
            )

    # get branch name
    git_branch = git_ref.split("/")[-1] if git_ref is not None else None

    # set defaults

    # use repo name as stack prefix default (without <user>/ prefix)
    cf_stack_prefix = cf_stack_prefix or git_repo_name.split("/")[-1]

    # use branch name as env name default
    env_name = env_name or git_branch

    # other necessary context settings

    # change working dir when running in github actions
    if is_github_action:
        os.chdir(github_workspace_dir)

    # run the actual deployment
    dep = Deployment(
        cf_stack_prefix=cf_stack_prefix,
        cf_template_path=cf_template_path,
        cf_parameter_overrides=cf_parameter_overrides,
        build_params=build_params,
        env_name=env_name,
        ecr_keep_last_n_images=ecr_keep_last_n_images,
        git_branch=git_branch,
        git_commit=git_commit,
        aws_region=aws_region,
    )
    asyncio.run(dep.run())


if __name__ == "__main__":
    github_action_handler()
