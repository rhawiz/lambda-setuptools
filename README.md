# lambda-setuptools

####A Command extension to setuptools that builds an AWS Lambda compatible zip file and uploads it to an S3 bucket

Simply add `setup_requires=['lambda_setuptools']` as an attribute to your _setup.py_ file

This extension adds two new commands to setuptools:

1. **ldist**
    * Usage: `ldist`
        * Effect: This will build (using _bdist_wheel_) and install your package, along with all of the dependencies in _install_requires_
            * It is _highly_ recommended that you **DO NOT** include _boto3_ or _botocore_ in your _install_requires_ dependencies as these are provided by the AWS Lambda environment. Include them at your own peril! 
            * The result will be in _dist/[your-package-name]-[version].zip_ (along with your wheel)
2. **lupload**
    * Usage: `lupload --access-key=<my_access_key> --secret-access-key=<my_secret> --s3-bucket=<my_S3_bucket> --kms-key-id=<my_KMS_key> --s3-prefix=<my_S3_key_prefix>`
        * Effect: This will build (using _ldist_) and upload the resulting ZIP file to the specified S3 bucket
            * _kms-key-id_ is optional. If it is not provided, standard AES256 encryption will be used
            * _s3-prefix_ is optional. If it is not provided, the ZIP file will be uploaded to the root of the S3 bucket
3. **ldeploy**
    * Usage `python setup.py ldeploy --swagger-path <swagger_spec_path> --deploy-stage <stage_name> --access-key=<my_access_key> --secret-access-key=<my_secret> --vpc-subnets=<SUBNET_IDS> --vpc-security-groups=<SECURITY_GROUP_IDS> --role=<AWS_ROLE> --region=<AWS_REGION>`
        * Effect: This will build (using _ldist_) and upload to AWS with the function name defined in `operationId` for each path and will map the lambda functions to each gateway if swagger-path is defined. If deploy-stage is defined, a new stage of that name will be created and the API will be deployed.
            * *access-key*            Required only if default access key is not set. The access key to use to upload. If not provided, default access key set in environment variables will be use if set, otherwise will fail.
            * *secret-access-key*     Required only if default secret key is not set. The access key to use to upload. If not provided, default secret key set in environment variables will be use if set, otherwise will fail.
            * *swagger-path*          Optional. Path to swagger specification file (YAML or JSON). If not provided, api gateway will not be created.
            * *deploy-stage*          Optional. Name of the deployment stage when deploying API gateway
            * *vpc-subnets*           Optional. VPC Configuration list of subnet ids separated by a comma
            * *vpc-security-groups*   Optional. VPC Configuration list of security group ids separated by a comma
            * *role*                  Required only when creating API gateway (i.e. if swagger-path is defined) AWS Gateway role to use when creating API gateway
            * *region*                Optional. AWS region to use. If not provided, default region set in environment variables will be use if set, otherwise will fail.


1. **lambda_function**
    * Usage: `lambda_function=[<my_package>.<some_module>:<handler_name/swagger_path_operation_id>]`
    * Effect: ldist will create a root-level python module named *<package_name>_function.py* where package_name is derived from the _name_ attribute. This created module will simply redefine all your defined lambda handler function at the root-level
    * Example:
```python
lambda_function=[
        "lambda_functions.aggregation_handler:aggregation",
        "lambda_functions.polygon_handler:polygon",
        "lambda_functions.distance_handler:distance"
        ]
```
2. **lambda_module**
    * Usage: `lambda_module=<some_module>`
    * Effect: ldist adds the named module to the list of _py_modules_ to install, normally at the root level
3. **lambda_package**
    * Usage: `lambda_package=<some_dir>`
    * Effect: ldist will copy the contents of the provided directory into the root level of the resulting lambda distribution. The provided directory **MUST NOT** have an *\_\_init__.py* in it (e.g. - it can't be a real package)
4. **lambda_config**
    * Usage: `lambda_config=<dict_lambda_configuration>`
    * Effect: This configuration will be used when creating the lambda functions on AWS and must adhere to [boto3 configuration](http://boto3.readthedocs.io/en/latest/reference/services/lambda.html#Lambda.Client.create_function).
    * Example:
```python
lambda_config={
    "Runtime": "python3.6",
    "Timeout": 60,
    "MemorySize": 128,
    "Publish": True
}
```

All _ldist_ attributes can be used in the same setup() call. It is up to the user to ensure that you don't step all over yourself...

Note that all other commands and attributes in setup.py will still work the way you expect them to.

Enjoy!

