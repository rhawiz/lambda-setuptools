import os
from copy import copy

import boto3
from distutils import log

import logging
import yaml
from distutils.errors import DistutilsArgError, DistutilsOptionError, DistutilsSetupError, DistutilsExecError
from jsonschema import ValidationError
from setuptools import Command
from swagger_spec_validator import SwaggerValidationError
from swagger_spec_validator.validator20 import validate_spec
from yaml.scanner import ScannerError


def validate_lambda_config(dist, attr, value):
    """Validate lambda config, if not passed into setup then set default config"""
    config = {
        "Runtime": "python3.6",
        "Timeout": 60,
        "MemorySize": 128,
        "Publish": True
    }
    if value is not None and isinstance(value, dict):
        config.update(value)

    setattr(dist, attr, config)


def validate_and_set_swagger_dict(dist, attr, value):
    """Validate swagger specification and set dist swagger_dict attribute"""
    if value is None:
        setattr(dist, 'swagger_dict', None)
        return

    swagger_dict = value
    if isinstance(value, dict):
        swagger_dict = value
    elif isinstance(value, str):
        contents = value
        if os.path.exists(value):
            contents = open(value, 'r').read()
        from json import JSONDecodeError
        try:
            swagger_dict = yaml.load(contents)
        except (ScannerError, JSONDecodeError, ValueError):
            raise DistutilsSetupError('Not a valid swagger definition or file path.')
    try:
        validate_spec(swagger_dict)
    except (SwaggerValidationError, ValidationError, JSONDecodeError) as e:
        raise DistutilsSetupError('Not a valid swagger definition. {}'.format(e))

    setattr(dist, 'swagger_dict', swagger_dict)


class LDeploy(Command):
    description = 'Create API gateway from a swagger specification and create lambda functions \
    from the result of the ldist command and map to the endpoints'

    user_options = [
        # The format is (long option, short option, description).
        ('access-key=', None,
         'The access key to use to upload. If not provided, default access key set in environment variables will be use if set, otherwise will fail.'),
        ('secret-access-key=', None,
         'The access key to use to upload. If not provided, default secret key set in environment variables will be use if set, otherwise will fail.'),
        ('swagger-path=', None, 'Path to swagger specification file (YAML or JSON)'),
        ('deploy-stage=', None, 'Name of the deployment stage'),
        ('vpc-subnets=', None, 'VPC Configuration list of subnet ids separated by a comma'),
        ('vpc-security-groups=', None, 'VPC Configuration list of security group ids separated by a comma'),
        ('role=', None, 'AWS Gateway role to use when creating API gateway'),
        ('region=', None,
         'AWS region to use. If not provided, default region set in environment variables will be use if set, otherwise will fail.')
    ]

    def initialize_options(self):
        """Set default values for options."""
        session = boto3.Session()

        default_access_key = None
        default_secret_access_key = None
        default_region = None

        log.info(session)

        if session is not None:
            try:
                default_access_key = session.get_credentials().access_key
                default_secret_access_key = session.get_credentials().secret_key
                default_region = session.region_name
            except Exception as e:
                log.error(e)

        # Each user option must be listed here with their default value.
        setattr(self, 'access_key', default_access_key)
        setattr(self, 'secret_access_key', default_secret_access_key)
        setattr(self, 'swagger_path', None)
        setattr(self, 'deploy_stage', None)
        setattr(self, 'vpc_subnets', None)
        setattr(self, 'vpc_security_groups', None)
        setattr(self, 'role', None)
        setattr(self, 'region', default_region)

    def finalize_options(self):
        """Post-process options."""

        if getattr(self, 'access_key') is None or getattr(self, 'secret_access_key') is None:
            raise DistutilsOptionError('access-key and secret-access-key are required or \
                                            default values need to be setup by running `aws configure`')
        if getattr(self, 'region') is None:
            raise DistutilsSetupError(
                'region must either be provided or default value need to be setup by running `aws configure`')

    def run(self):
        """Run command."""
        self.run_command('ldist')
        ldist_cmd = self.get_finalized_command('ldist')
        dist_path = getattr(ldist_cmd, 'dist_path', None)
        dist_name = getattr(ldist_cmd, 'dist_name', None)
        swagger_path = getattr(self, 'swagger_path')
        validate_and_set_swagger_dict(self, 'swagger_path', swagger_path)
        if dist_path is None or dist_name is None:
            raise DistutilsArgError('\'ldist\' missing attributes')

        gw_lambda_mapping = self._create_or_update_lambda_functions(ldist_cmd)

        # If swagger_dict is not defined, do not create API Gateway
        if getattr(self, 'swagger_dict') is not None:
            self._create_and_deploy_api(gw_lambda_mapping)

    def _create_swagger_doc(self, lambda_mapping):
        log.info("Creating swagger specification")
        swagger_dict = copy(getattr(self, 'swagger_dict'))
        region = getattr(self, 'region', None)
        paths = swagger_dict["paths"]

        for path in paths.keys():
            path_info = paths[path]
            for method in path_info.keys():
                method_info = path_info[method]
                operation_id = method_info.get("operationId")
                lambda_info = lambda_mapping.get(operation_id)
                if lambda_info is not None:
                    function_arn = lambda_info.get("FunctionArn")
                    uri = "arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{arn}/invocations".format(
                        arn=function_arn, region=region)
                    if "x-amazon-apigateway-integration" in method_info:
                        method_info["x-amazon-apigateway-integration"]["uri"] = uri

        return swagger_dict

    def _create_or_update_lambda_functions(self, ldist_cmd):
        lambda_function_names = getattr(ldist_cmd, 'lambda_function_names', None)
        dist_path = getattr(ldist_cmd, 'dist_path', None)
        region = getattr(self, 'region', None)

        role = getattr(self, 'role', None)
        vpc_subnets = getattr(self, 'vpc_subnets', None)
        vpc_security_groups = getattr(self, 'vpc_security_groups', None)

        iam_client = boto3.client(
            'iam',
            aws_access_key_id=getattr(self, 'access_key'),
            aws_secret_access_key=getattr(self, 'secret_access_key'),
        )

        arn_role = iam_client.get_role(RoleName=role)['Role']['Arn']

        lambda_client = boto3.client(
            'lambda',
            region_name=region,
            aws_access_key_id=getattr(self, 'access_key'),
            aws_secret_access_key=getattr(self, 'secret_access_key'),
        )

        lambda_mapping = {}

        lambda_config = getattr(self.distribution, 'lambda_config', {})

        log.info("Creating lambda functions.")
        for function_name in lambda_function_names.keys():
            handler = lambda_function_names.get(function_name)

            try:
                lambda_client.get_function(FunctionName=function_name)
                exists = True
            except Exception:
                exists = False

            zipfile = open(dist_path, 'rb')

            config = copy(lambda_config)
            config["FunctionName"] = function_name
            config["Role"] = arn_role
            config["Handler"] = handler
            config["Code"] = {'ZipFile': zipfile.read()}

            vpc_config = {}

            if vpc_subnets:
                vpc_config["SubnetIds"] = [a.strip() for a in vpc_subnets.split(',')]
            if vpc_security_groups:
                vpc_config["SecurityGroupIds"] = [a.strip() for a in vpc_security_groups.split(',')]

            if len(vpc_config) is not 0:
                config["VpcConfig"] = vpc_config

            if exists:
                log.info("Updating lambda function '{}' with new configuration.".format(function_name))
                code_config = {
                    "FunctionName": function_name,
                    "ZipFile": config.pop("Code").pop("ZipFile"),
                    "Publish": config.pop("Publish")
                }
                try:
                    lambda_client.update_function_code(**code_config)
                    r = lambda_client.update_function_configuration(**config)
                    arn = r.get("FunctionArn", "")
                    log.info("\tsuccessfully updated: {}".format(arn))

                except Exception as e:
                    raise DistutilsExecError("Failed to update lambda function: {}".format(e))
            else:
                log.info("Creating lambda function '{}'.".format(function_name))
                try:
                    r = lambda_client.create_function(**config)
                    log.info("successfully created: {}".format(r.get("FunctionArn", "")))

                except Exception as e:
                    raise DistutilsExecError("Failed to create lambda function: {}".format(e))

            zipfile.close()

            lambda_mapping[function_name] = r
        return lambda_mapping

    def _create_and_deploy_api(self, gw_lambda_mapping):
        swagger_doc = self._create_swagger_doc(gw_lambda_mapping)
        log.info("Creating API gateway from swagger specification")

        region = getattr(self, 'region', None)

        gateway_client = boto3.client('apigateway', region_name=region, aws_access_key_id=getattr(self, 'access_key'),
                                      aws_secret_access_key=getattr(self, 'secret_access_key'))
        deploy_stage = getattr(self, 'deploy_stage')

        try:
            import json
            resp = gateway_client.import_rest_api(failOnWarnings=True, body=json.dumps(swagger_doc))
            if deploy_stage is not None:
                rest_id = resp.get('id')

                try:
                    log.info("Creating stage {} and deploying API.".format(deploy_stage))
                    gateway_client.create_deployment(
                        restApiId=rest_id,
                        stageName=deploy_stage)

                    log.info("Updating permission")

                    account_id = boto3.client('sts', aws_access_key_id=getattr(self, 'access_key'),
                                              aws_secret_access_key=getattr(self,
                                                                            'secret_access_key')).get_caller_identity().get(
                        'Account')
                    for function_name in gw_lambda_mapping.keys():
                        log.info("\tUpdating permissions for function {}".format(function_name))
                        lambda_client = boto3.client('lambda', region_name=region,
                                                     aws_access_key_id=getattr(self, 'access_key'),
                                                     aws_secret_access_key=getattr(self, 'secret_access_key'))
                        source_arn = "arn:aws:execute-api:{region}:{account_id}:{rest_id}/{deploy_stage}/ANY/{function_name}".format(
                            deploy_stage=deploy_stage,
                            region=region,
                            account_id=account_id,
                            rest_id=rest_id,
                            function_name=function_name
                        )
                        log.info(source_arn)

                        try:
                            lambda_client.remove_permission(
                                FunctionName=function_name,
                                StatementId='api-gateway-execute'
                            )
                        except Exception:
                            pass

                        lambda_client.add_permission(
                            FunctionName='arn:aws:lambda:{region}:{account_id}:function:{function_name}'.format(
                                region=region, account_id=account_id, function_name=function_name),
                            StatementId='api-gateway-execute',
                            Action='lambda:InvokeFunction',
                            Principal='apigateway.amazonaws.com',
                            SourceArn=source_arn
                        )

                except Exception as e:
                    log.error("Failed to deploy API: {}".format(e))


        except Exception as e:
            log.error(e)
            raise DistutilsSetupError("Failed to import swagger specification")
