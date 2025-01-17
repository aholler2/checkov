
from checkov.common.models.enums import CheckCategories, CheckResult
from checkov.kubernetes.checks.resource.base_spec_check import BaseK8Check

strongCiphers = ["TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256","TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256","TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305","TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384","TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305","TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384","TLS_RSA_WITH_AES_256_GCM_SHA384","TLS_RSA_WITH_AES_128_GCM_SHA256"]


class ApiServerStrongCryptographicCiphers(BaseK8Check):
    def __init__(self):
        id = "CKV_K8S_105"
        name = "Ensure that the API Server only makes use of Strong Cryptographic Ciphers"
        categories = [CheckCategories.KUBERNETES]
        supported_entities = ['containers']
        super().__init__(name=name, id=id, categories=categories, supported_entities=supported_entities)

    def get_resource_id(self, conf):
        return f'{conf["parent"]} - {conf["name"]}' if conf.get('name') else conf["parent"]

    def scan_spec_conf(self, conf):
        if "command" in conf:
            if "kube-apiserver" in conf["command"]:
                for command in conf["command"]:
                    if command.startswith("--tls-cipher-suites"):
                        value = command.split("=")[1]    
                        ciphers = value.split(",")
                        for cipher in ciphers:
                            if cipher not in strongCiphers:
                                return CheckResult.FAILED

        return CheckResult.PASSED


check =  ApiServerStrongCryptographicCiphers()
