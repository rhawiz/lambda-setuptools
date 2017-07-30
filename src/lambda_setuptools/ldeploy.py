import os
from copy import copy
from json import JSONDecodeError

import boto3

import yaml
from distutils.errors import DistutilsArgError, DistutilsOptionError, DistutilsSetupError, DistutilsExecError
from jsonschema import ValidationError
from setuptools import Command
from swagger_spec_validator import SwaggerValidationError
from swagger_spec_validator.validator20 import validate_spec
from yaml.scanner import ScannerError


def validate_aws_role(dist, attr, value):

    setattr(dist, "aws_role", value)


def validate_lambda_config(dist, attr, value):
    """Validate lambda config, if not passed into setup then set default config"""

    if value is None:
        value = {
            "Runtime": "python3.6",
            "Timeout": 123,
            "MemorySize": 123,
            "Publish": True
        }
    setattr(dist, attr, value)


def validate_aws_region(dist, attr, value):
    if value is None:
        session = boto3.Session()
        if session.region_name is None:
            raise DistutilsSetupError(
                'aws_region must either be set or default value need to be setup by running `aws configure`')
        else:
            setattr(dist, 'aws_region', session.region_name)


def validate_swagger(dist, attr, value):
    """Validate swagger specification and set dist swagger_dict attribute"""

    swagger_dict = value
    if isinstance(value, dict):
        swagger_dict = value
    elif isinstance(value, str):
        contents = value
        if os.path.exists(value):
            contents = open(value, 'r').read()
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
        ('access-key=', None, 'The access key to use to upload'),
        ('secret-access-key=', None, 'The secret access to use to upload')
    ]

    def initialize_options(self):
        """Set default values for options."""
        session = boto3.Session()

        default_access_key = session.get_credentials().access_key
        default_secret_access_key = session.get_credentials().secret_key

        # Each user option must be listed here with their default value.
        setattr(self, 'access_key', default_access_key)
        setattr(self, 'secret_access_key', default_secret_access_key)

    def finalize_options(self):
        """Post-process options."""

        if getattr(self, 'access_key') is None or getattr(self, 'secret_access_key') is None:
            raise DistutilsOptionError('access-key and secret-access-key are required or \
                                            default values need to be setup by running `aws configure`')

    def run(self):
        """Run command."""
        self.run_command('ldist')
        ldist_cmd = self.get_finalized_command('ldist')
        dist_path = getattr(ldist_cmd, 'dist_path', None)
        dist_name = getattr(ldist_cmd, 'dist_name', None)
        if dist_path is None or dist_name is None:
            raise DistutilsArgError('\'ldist\' missing attributes')

        gw_lambda_mapping = self._create_lambda_functions(ldist_cmd)
        swagger_doc = self._create_swagger_doc(gw_lambda_mapping)
        gateway_client = boto3.client('apigateway', getattr(self, 'aws_region', None))
        print(swagger_doc)
        gateway_client.import_rest_api(failOnWarnings=True, body=swagger_doc)

    def _create_swagger_doc(self, lambda_mapping):
        swagger_dict = getattr(self, 'swagger_dict', None)
        region = getattr(self, 'aws_region', None)

        paths = swagger_dict.get("paths")
        for endpoint_key in lambda_mapping.keys():

            for method in paths.values():
                for path_info in method.values():
                    operation_id = method.get("operationId")
                    if operation_id == endpoint_key:
                        lambda_info = lambda_mapping.get(endpoint_key)
                        function_arn = lambda_info.get("Configuration").get("FunctionArn")
                        uri = "{}/{}/invocations".format(
                            "arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions".format(region=region),
                            function_arn)
                        path_info["x-amazon-apigateway-integration"]["uri"] = uri
        return swagger_dict

    def _create_lambda_functions(self, ldist_cmd):
        lambda_endpoints = getattr(ldist_cmd, 'lambda_endpoints', None)
        dist_path = getattr(ldist_cmd, 'dist_path', None)
        dist_name = getattr(ldist_cmd, 'dist_name', None)
        region = getattr(self, 'aws_region', None)
        role = getattr(self, 'aws_role', None)

        iam_client = boto3.client('iam')

        arn_role = iam_client.get_role(RoleName=role)['Role']['Arn']

        lambda_client = boto3.client('lambda', region)

        zipfile = open(os.path.join(dist_path, dist_name, 'rb'))
        lambda_mapping = {}
        lambda_config = getattr(self, 'lambda_config', {})
        for endpoint in lambda_endpoints.keys():
            handler = lambda_endpoints.get(endpoint)
            function_name = "{}Handler".format(endpoint)
            config = copy(lambda_config)
            config["FunctionName"] = function_name
            config["Role"] = arn_role
            config["Handler"] = handler
            config["Code"] = {'ZipFile': zipfile.read()}

            try:
                lambda_client.get_function(FunctionName=function_name)
                exists = True
            except Exception:
                exists = False
            if exists:
                try:
                    r = lambda_client.update_function_configuration(**config)
                    lambda_mapping[endpoint] = r
                except Exception as e:
                    raise DistutilsExecError("Failed to update lambda function with error {}".format(e))
            elif not exists:
                try:
                    r = lambda_client.create_function(**config)
                    lambda_mapping[endpoint] = r
                except Exception as e:
                    raise DistutilsExecError("Failed to create lambda function with error {}".format(e))

        return lambda_mapping

#
# class MockDist():
#     swagger_dict = None
#
#
# d = MockDist()
#
# sp = "/home/rawand/PycharmProjects/coredb-service-aggregation/coredb-service-aggregation-swagger.json"
# validate_swagger(d, None, sp)
# print(d.swagger_dict)
