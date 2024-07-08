# CloudFormation Deploy Action

⚠️ WORK IN PROGRESS ⚠️

This action deploys a Docker Compose file to AWS ECS using CloudFormation.
Based ECS Compose X, this action handles the following:
- Builds local Docker images
- Creates ECR repositories for locally built Docker images
- Pushes local Docker images to ECR

## Example Usage

```yaml
name: Deploy

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Deploy to AWS
        uses: tomas-polach/deploy-compose-to-aws@v1
        with:
          aws-region: 'eu-west-1'
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

## Todos

- [ ] Add examples
- [ ] Print CF errors in action results
- [ ] Provide CF outputs in the action results

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
