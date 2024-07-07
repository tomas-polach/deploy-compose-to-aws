import sys
import os
from time import sleep
import boto3


class DNSAndSSLCertManager:
    def __init__(
            self,
            cert_region_name: str,
            cert_role_arn: str | None = None,
            domain_role_arn: str | None = None,
    ):
        self.region_name = region_name

        if domain_role_arn is None:
            self.route53_client = boto3.client('route53')
        else:
            # if domain is registered in another aws account, assume role
            access_key_id, secret_access_key, session_token = self._assume_role(domain_role_arn)
            self.route53_client = boto3.client(
                'route53',
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                aws_session_token=session_token,
            )

        if cert_role_arn is None:
            self.acm_client = boto3.client('acm', region_name=cert_region_name)
        else:
            # if cert is created in another aws account, assume role
            access_key_id, secret_access_key, session_token = self._assume_role(cert_role_arn)
            self.acm_client = boto3.client(
                'acm',
                region_name=cert_region_name,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                aws_session_token=session_token,
            )

    @staticmethod
    def _assume_role(role_arn: str) -> tuple[str, str, str]:
        sts_client = boto3.client('sts')
        assumed_role = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="DNSUpdateSession"
        )
        return (
            assumed_role['Credentials']['AccessKeyId'],
            assumed_role['Credentials']['SecretAccessKey'],
            assumed_role['Credentials']['SessionToken'],
        )

    def _get_hosted_zone_id(self, subdomain):
        # List all hosted zones
        hosted_zones = self.route53_client.list_hosted_zones_by_name()

        # Find the hosted zone ID for the given subdomain
        for zone in hosted_zones['HostedZones']:
            if subdomain.endswith(zone['Name'].rstrip('.')):
                return zone['Id'].split('/')[-1]

        raise Exception(f"No hosted zone found for subdomain: {subdomain}")

    def _upsert_cname_record(self, hosted_zone_id, subdomain: str, target: str):
        change_batch = {
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": subdomain,
                        "Type": "CNAME",
                        "TTL": 300,
                        "ResourceRecords": [
                            {
                                "Value": target
                            }
                        ]
                    }
                }
            ]
        }

        response = self.route53_client.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch=change_batch
        )

        return response

    def add_cname_record(self, subdomain, target):
        # Retrieve hosted zone ID
        hosted_zone_id = self._get_hosted_zone_id(subdomain)
        # Upsert CNAME record
        self._upsert_cname_record(hosted_zone_id, subdomain, target)

    def get_or_create_ssl_cert(self, subdomain: str) -> str:
        # List all certificates
        paginator = self.acm_client.get_paginator('list_certificates')
        cert_arn = None
        for page in paginator.paginate(CertificateStatuses=['PENDING_VALIDATION', 'ISSUED']):
            for cert in page['CertificateSummaryList']:
                cert_details = self.acm_client.describe_certificate(CertificateArn=cert['CertificateArn'])
                cert_domains = cert_details['Certificate']['SubjectAlternativeNames']
                if any(domain == f"*.{subdomain.split('.')[-2]}.{subdomain.split('.')[-1]}" for domain in cert_domains):
                    print(f"Found existing certificate: {cert['CertificateArn']}")
                    cert_arn = cert['CertificateArn']

        if cert_arn is None:
            # If no suitable certificate found, create a new one
            response = self.acm_client.request_certificate(
                DomainName=f"*.{subdomain.split('.')[-2]}.{subdomain.split('.')[-1]}",
                ValidationMethod='DNS',
                # SubjectAlternativeNames=[]
            )
            cert_arn = response['CertificateArn']
            print(f"Requested new certificate: {cert_arn}")

        # Wait for required DNS validation records to be available by certificate creation API (takes ca 5-15 seconds)
        validation_options = None
        while validation_options is None:
            cert_details = self.acm_client.describe_certificate(CertificateArn=cert_arn)
            if 'DomainValidationOptions' in cert_details['Certificate']:
                validation_options = cert_details['Certificate']['DomainValidationOptions']
            else:
                print(f"Waiting for DNS validation set ...")
                sleep(5)

        hosted_zone_id = self._get_hosted_zone_id(subdomain)

        # create DNS validation records
        change_batch = {"Changes": []}
        for option in validation_options:
            if 'ResourceRecord' in option:
                validation_record = option['ResourceRecord']
                print(f"Creating DNS validation record: {validation_record}")
                change_batch["Changes"].append({
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": validation_record['Name'],
                        "Type": validation_record['Type'],
                        "TTL": 300,
                        "ResourceRecords": [
                            {
                                "Value": validation_record['Value']
                            }
                        ]
                    }
                })
        self.route53_client.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch=change_batch
        )

        # Wait for the certificate to be validated
        print(f"Waiting for certificate {cert_arn} to be validated...")
        waiter = self.acm_client.get_waiter('certificate_validated')
        try:
            waiter.wait(CertificateArn=cert_arn)
            print(f"Certificate {cert_arn} successfully validated.")
        except Exception as e:
            print(f"Certificate validation failed: {e}")

        return cert_arn


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python script_name.py <cname_domain> <cname_domain_target> <region>")
        sys.exit(1)

    cname_domain = sys.argv[1]
    cname_domain_target = sys.argv[2]
    region_name = sys.argv[3]

    m = DNSAndSSLCertManager(
        cert_region_name = region_name,
        # cert_role_arn = os.getenv('ROLE_ARN'),
        domain_role_arn = os.getenv('DOMAIN_ROLE_ARN'),
    )
    cert_arn = m.get_or_create_ssl_cert(cname_domain)
    m.add_cname_record(cname_domain, cname_domain_target)
    print(f"SSL certificate ARN: {cert_arn}")
