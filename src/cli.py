import os
import sys
import argparse
import asyncio
from pprint import pprint as pp

from src.deploy import Deployment


def main():
    print('AWS_ACCESS_KEY_ID', os.getenv('AWS_ACCESS_KEY_ID'))

    # just print all the raw args from the cli
    parser = argparse.ArgumentParser(description='RuDeploy to Docker Compose to AWS')

    parser.add_argument('--aws-region', type=str, required=True, help='The AWS region', default=os.getenv('INPUT_AWS_REGION'))

    parser.add_argument('--cf-stack-prefix', type=str, required=False, help='Prefix for the Cloudformation Stack')
    parser.add_argument('--environment', type=str, required=False,
                        help='The environment (will be added as suffix to the stack name)')

    parser.add_argument('--elb-domain', type=str, required=False, help='The domain to map to elastic load balancer')
    parser.add_argument('--elb-domain-role-arn', type=str, required=False, help='The domain role ARN')

    parser.add_argument('--docker-compose-path', type=str, required=False,
                        help='The docker compose path')
    parser.add_argument('--aws-compose-path', type=str, required=False,
                        help='The AWS compose path')

    args = parser.parse_args()
    # Convert argument names with dashes to underscores
    args_dict = {
        k.replace('-', '_'): v
        for k, v in vars(args).items()
        if v != '' and v is not None
    }

    print('args_dict:')
    pp(args_dict)

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
    if 'environment' not in args_dict and git_branch is not None:
        args_dict['environment'] = git_branch

    # change working dir
    os.chdir('/github/workspace')

    dep = Deployment(**args_dict)
    asyncio.run(dep.run())


if __name__ == "__main__":
    main()
