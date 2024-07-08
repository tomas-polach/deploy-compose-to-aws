# CloudFormation Deploy Action

⚠️ WORK IN PROGRESS ⚠️

This action deploys a Docker Compose file to AWS ECS using CloudFormation.
Based ECS Compose X, this action handles the following:
- Builds local Docker images
- Creates ECR repositories for locally built Docker images
- Pushes local Docker images to ECR

## Inputs

### `cf-stack-name`

**Optional** CloudFormation stack name. Defaults to the repository name.

### `env-name`

**Optional** Environment name. Defaults to the branch name.

## Example Usage

```yaml
name: Deploy

on:
  push:
    branches:
      - main
      - 'feature/*'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v2

      - name: Deploy to AWS
        uses: tomas-polach/deploy-compose-to-aws@v1
        with:
          aws_region: 'eu-west-1'
          aws_access_key_id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws_secret_access_key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

## What this does under the hood

1. compile future docker image URIs for locally built docker images
1. CloudFormation: ci stack (ECR repos for locally built docker images and ci bucket)
     - note: ci cf template can't be uploaded to S3 because the ci bucket will be created in the ci stack
1. Docker:
     - login to ECR
     - build local images, tag and push to ECR
     - generate docker-compose.override.yaml which will add docker image URIs to services with local docker builds, so that docker knows where to push the locally built images to
1. create SSL cert (has to be in the same account as the elb?)
1. generate CloudFormation: main stack
1. deploy cloud formation
1. create CNAME record with ELB as target
1. delete temp dir
