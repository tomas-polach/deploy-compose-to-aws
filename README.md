# CloudFormation Deploy Action

This action deploys to AWS CloudFormation using Python 3.11 and AWS CLI.

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
