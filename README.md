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
        uses: tomas-polach/deploy-compose-to-aws@main
        id: deploy
        env:
          AWS_REGION: 'eu-west-1'
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}

      # optional: Use outputs from the deployment
      - name: Extract outputs
        id: deploy-outputs
        run: |
          load_balancer_dns_name=$(jq -r '.by_output_key.publicalbDNSName' "${{ steps.deploy.outputs.cf-output-path }}")
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

- [ ] add cf_disable_rollback param
- [ ] Print CF errors in action UI
- [ ] Add examples
- [ ] PR to ECS Compose X, then use official dependency

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
