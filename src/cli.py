import os
import sys
import argparse
import asyncio
from pprint import pprint as pp

from src.deploy import Deployment


def main():
    # just print all the raw args from the cli
    for arg in sys.argv:
        print('ARGGGG:', arg)

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
    args = {k.replace('-', '_'): v for k, v in vars(args).items()}

    # Use the provided project_name or default to the repository name
    if args.cf_stack_prefix is None:
        args.cf_stack_prefix = os.getenv('GITHUB_REPOSITORY', 'default-repo').split('/')[-1]

    # Use the provided environment or default to the branch name
    if args.environment is None:
        args.environment = os.getenv('GITHUB_REF', 'refs/heads/default-branch').split('/')[-1]

    # remove None values from args
    kwargs = {k: v for k, v in vars(args).items() if v is not None}

    # change working dir
    os.chdir('/github/workspace')

    dep = Deployment(**kwargs)
    asyncio.run(dep.run())


if __name__ == "__main__":
    main()
