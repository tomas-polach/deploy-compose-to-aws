# CloudFormation Deploy Action

⚠️ WORK IN PROGRESS ⚠️

This action deploys a Docker Compose file to AWS ECS using CloudFormation.
Based ECS Compose X, this action handles the following:
- Builds local Docker images using buildx and QEMU
- Uses cache to speed up builds
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

      - name: Build Docker Images
        uses: tomas-polach/deploy-compose-to-aws@ecr-build
        id: build
        env:
          # required
          AWS_REGION: 'eu-west-1'
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
        with:
          # (optional) will replace "${my_placeholder}" with "foobar" in the ecs compose x YAML file
          builds: |
            django:
              build:
                context: .
                dockerfile: Dockerfile
                args:
                  - NODE_ENV=production
              platform: linux/amd64
          cf-stack-prefix: my-stack
          cf-template-path: infra/main.yaml
          cf-parameter-overrides: |
            domain: mydomain.com

      # optional: Use outputs from the deployment
      - name: Extract outputs
        id: deploy-outputs
        run: |
          load_balancer_dns_name=$(jq -r '.by_output_key.publicalbDNSName' "${{ steps.build.outputs.image-url-by-service-json }}")
          echo "load-balancer-dns-name=$load_balancer_dns_name" >> $GITHUB_OUTPUT

      - name: Use outputs
        run: |
          echo "Load balancer DNS name: ${{ steps.deploy-outputs.outputs.load-balancer-dns-name }}"

      # optional: allow download of cloudformation templates and outputs for debugging
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: deployment
          path: _deployment_tmp
          retention-days: 7
```

## Todos

- [ ] PR to ECS Compose X, then use official dependency
- [ ] add cf_disable_rollback param
- [ ] Print CF errors in action UI
- [ ] Add examples
- [ ] Reuse image if same across services (instead of building multiple times)

## What this does under the hood

1. CloudFormation: deploy ci stack (ECR repos for locally built docker images and S3 bucket)
     - note: ci cf template can't be uploaded to S3 because the ci bucket will be created in the ci stack
1. Docker:
     - login to ECR
     - build local images, tag and push to ECR
1. generate CloudFormation: main stack
1. deploy cloud formation

## Format code

```bash
black src
```
