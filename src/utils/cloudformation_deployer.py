from time import sleep
import boto3
from src.utils.logger import get_logger


logger = get_logger(__name__)


class CloudFormationDeployer:
    _default_capabilities = [
        "CAPABILITY_IAM",
        "CAPABILITY_NAMED_IAM",
        "CAPABILITY_AUTO_EXPAND",
    ]

    def __init__(self, region_name: str):
        self.cf_client = boto3.client("cloudformation", region_name=region_name)
        self.sts_client = boto3.client("sts")

    def get_account_id(self) -> str:
        identity = self.sts_client.get_caller_identity()
        return identity["Account"]

    def stack_exists(self, stack_name) -> bool:
        try:
            self.cf_client.describe_stacks(StackName=stack_name)
            return True
        except self.cf_client.exceptions.ClientError as e:
            if "does not exist" in str(e):
                return False
            raise  # Re-raise the exception if it's not a "does not exist" error

    def _get_cloudformation_stack_by_name(self, stack_name: str):
        response = self.cf_client.describe_stacks(StackName=stack_name)
        for stack in response["Stacks"]:
            if stack["StackName"] == stack_name:
                return stack
        raise FileNotFoundError(f"Stack not found: {stack_name}")

    def wait_for_stack_completion(
        self,
        stack_name: str,
        timeout=2 * 60 * 60,  # in seconds
        sleep_time=10,
    ) -> None:
        elapsed_time = 2  # initial delay
        sleep(elapsed_time)  # wait before checking the stack status for the first time
        stack_status = None
        while elapsed_time < timeout:
            stack_status = self._get_cloudformation_stack_by_name(stack_name)[
                "StackStatus"
            ]
            if stack_status.endswith("_IN_PROGRESS"):
                logger.debug(f"Current stack status: {stack_status}")
                sleep(sleep_time)
                elapsed_time += sleep_time
            elif stack_status.endswith("_COMPLETE") and not stack_status.endswith(
                "_ROLLBACK_COMPLETE"
            ):
                logger.debug(f"Stack operation finished with status: {stack_status}")
                return
            else:
                raise Exception(f"Stack operation failed with status: {stack_status}")

        raise TimeoutError(
            f"Timed out waiting for stack operation to complete. Last known status: {stack_status}"
        )

    def create_or_update_stack(
        self,
        stack_name: str,
        template_body: str | None = None,
        template_url: str | None = None,
        parameters: dict[str, str] = {},
        capabilities: list[str] | None = None,
    ) -> bool:
        """
        return True if the stack was created or updated, False if no changes were needed
        """
        # check if template_body or template_url is provided
        if not any([template_body, template_url]):
            raise ValueError("Either template_body or template_url must be provided")

        cf_method = (
            self.cf_client.update_stack
            if self.stack_exists(stack_name)
            else self.cf_client.create_stack
        )
        try:
            # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cloudformation/client/update_stack.html
            params = {
                "StackName": stack_name,
                "Parameters": [
                    {"ParameterKey": key, "ParameterValue": val}
                    for key, val in parameters.items()
                ],
                "Capabilities": capabilities or self._default_capabilities,
            }
            if template_body:
                params["TemplateBody"] = template_body
            elif template_url:
                params["TemplateURL"] = template_url
            cf_method(**params)
            logger.debug(f"Stack create/update initiated for stack: {stack_name}")
            self.wait_for_stack_completion(stack_name)
            return True
        except Exception as err:
            if "no updates are to be performed" in str(err).lower():
                logger.debug(f'Stack "{stack_name}" is up to date. No changes needed.')
                return False
            else:
                raise err

    def get_stack_outputs(self, stack_name: str) -> list[dict[str, str]]:
        response = self.cf_client.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0].get("Outputs", [])
        return outputs

    def get_nested_stacks(self, stack_name: str) -> list[str]:
        response = self.cf_client.describe_stack_resources(StackName=stack_name)
        nested_stacks = [
            resource["PhysicalResourceId"]
            for resource in response["StackResources"]
            if resource["ResourceType"] == "AWS::CloudFormation::Stack"
        ]
        return nested_stacks

    def get_nested_stack_outputs(self, stack_name: str):
        all_outputs = []

        # Get outputs of the current stack
        stack_outputs = self.get_stack_outputs(stack_name)
        all_outputs.extend(stack_outputs)

        # Get nested stacks
        nested_stacks = self.get_nested_stacks(stack_name)

        # Recursively get outputs of nested stacks
        for nested_stack in nested_stacks:
            nested_outputs = self.get_nested_stack_outputs(nested_stack)
            all_outputs.extend(nested_outputs)

        return all_outputs
