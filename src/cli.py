import os
import argparse
import asyncio

from src.deploy import Deployment


def main():
    parser = argparse.ArgumentParser(description='RuDeploy to Docker Compose to AWS')

    parser.add_argument('--aws_region', type=str, required=True, help='The AWS region')

    parser.add_argument('--cf_stack_prefix', type=str, required=False, help='Prefix for the Cloudformation Stack')
    parser.add_argument('--environment', type=str, required=False,
                        help='The environment (will be added as suffix to the stack name)')

    parser.add_argument('--domain', type=str, required=False, help='The domain')
    parser.add_argument('--domain_role_arn', type=str, required=False, help='The domain role ARN')
    parser.add_argument('--cert_role_arn', type=str, required=False, help='The certificate role ARN')

    parser.add_argument('--docker_compose_path', type=str, required=False,
                        help='The docker compose path')
    parser.add_argument('--aws_compose_path', type=str, required=False,
                        help='The AWS compose path')
    # parser.add_argument('--temp_dir', type=str, required=False, help='The temporary directory')
    # parser.add_argument('--mutable_tags', type=bool, required=False, help='Whether the tags are mutable')
    # parser.add_argument('--image_uri_format', type=str, required=False, help='The image URI format')

    args = parser.parse_args()

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
