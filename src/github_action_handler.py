import os
import json
import asyncio
from src.deploy import Deployment


def getenv(var_name: str, default=None):
    # returns None also if the value is an empty string
    value = os.getenv(var_name, default)
    return value if value != "" else default


def github_action_handler():
    cf_stack_prefix = getenv("INPUT_CF_STACK_PREFIX", None)
    env_name = getenv("INPUT_ENV_NAME", None)
    docker_compose_path = getenv("INPUT_DOCKER_COMPOSE_PATH", None)
    ecs_composex_path = getenv("INPUT_ECS_COMPOSEX_PATH", None)
    ecs_composex_subs = getenv("INPUT_ECS_COMPOSEX_SUBS", "{}")
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

    # parse ecs compose x substitutes
    try:
        ecs_composex_subs = json.loads(ecs_composex_subs)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON string provided for ECS Compose X substitutes")

    # convert ecr_keep_last_n_images to int
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
        env_name=env_name,
        docker_compose_path=docker_compose_path,
        ecs_composex_path=ecs_composex_path,
        ecs_composex_subs=ecs_composex_subs,
        ecr_keep_last_n_images=ecr_keep_last_n_images,
        git_branch=git_branch,
        git_commit=git_commit,
        aws_region=aws_region,
    )
    asyncio.run(dep.run())


if __name__ == "__main__":
    github_action_handler()
