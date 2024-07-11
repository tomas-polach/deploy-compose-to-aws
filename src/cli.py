import os
import json
import argparse
import asyncio
from src.deploy import Deployment


def main():
    # just print all the raw args from the cli
    parser = argparse.ArgumentParser(description='RuDeploy to Docker Compose to AWS')

    parser.add_argument('--cf-stack-prefix', type=str, required=False, help='Prefix for the Cloudformation Stack')
    parser.add_argument('--env-name', type=str, required=False,
                        help='The environment (will be added as suffix to the stack name)')

    parser.add_argument('--docker-compose-path', type=str, required=False,
                        help='The docker compose path')
    parser.add_argument('--ecs-compose-x-path', type=str, required=False,
                        help='The AWS compose path')

    parser.add_argument('--ecs-compose-x-sub', type=str, required=False,
                        default='{}',
                        help='ECS Compose X substitutes in the format key=value',
                        )

    parser.add_argument('--ecr-keep-last-n-images',
                        type=int,
                        default=10,
                        required=False, help='The number of images to keep in ECR')

    args = parser.parse_args()
    # Convert argument names with dashes to underscores
    args_dict = {
        k.replace('-', '_'): v
        for k, v in vars(args).items()
        if v != '' and v is not None
    }

    # Convert ECS Compose X substitutes JSON string to a dictionary
    if 'ecs_compose_x_sub' in args_dict:
        # validate the JSON string before converting to a dictionary
        try:
            args_dict['ecs_compose_x_sub'] = json.loads(args_dict['ecs_compose_x_sub'])
        except json.JSONDecodeError:
            raise ValueError('Invalid JSON string provided for ECS Compose X substitutes')

    # Get branch name and commit hash
    git_repo_name = os.getenv('GITHUB_REPOSITORY', None)
    if git_repo_name is not None:
        git_repo_name = git_repo_name.split('/')[-1]

    git_branch = os.getenv('GITHUB_REF', None)
    if git_branch is not None:
        git_branch = git_branch.split('/')[-1]
        args_dict['git_branch'] = git_branch

    git_commit = os.getenv('GITHUB_SHA', None)
    if git_commit is not None:
        args_dict['git_commit'] = git_commit

    # set defaults based on github action env vars

    # Use the provided project_name or default to the repository name
    if 'cf_stack_prefix' not in args_dict:
        args_dict['cf_stack_prefix'] = git_repo_name

    # Use the provided environment or default to the branch name
    if 'env_name' not in args_dict and git_branch is not None:
        args_dict['env_name'] = git_branch

    # change working dir when running in github actions
    if os.getenv('GITHUB_ACTIONS') == 'true':
        os.chdir('/github/workspace')

    # check if aws region is set
    if 'AWS_REGION' not in os.environ:
        raise ValueError('AWS_REGION environment variable is not set')
    # get aws region from env vars
    args_dict['aws_region'] = os.getenv('AWS_REGION')

    os.environ['AWS_DEFAULT_REGION'] = args_dict['aws_region']

    dep = Deployment(**args_dict)
    asyncio.run(dep.run())


if __name__ == "__main__":
    main()
