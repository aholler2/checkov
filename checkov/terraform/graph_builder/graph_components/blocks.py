import logging
import os
from copy import deepcopy

from checkov.common.util.consts import RESOLVED_MODULE_ENTRY_NAME
from checkov.terraform.graph_builder.graph_components.attribute_names import CustomAttributes
from checkov.terraform.graph_builder.graph_components.block_types import BlockType
from checkov.terraform.graph_builder.utils import remove_module_dependency_in_path
from checkov.terraform.checks.utils import utils
from checkov.terraform.checks.utils.utils import calculate_hash, decode_graph_property_value, join_trimmed_strings


class Block:
    def __init__(self, name, config, path, block_type, attributes, id='', source='', encode=False):
        """
            :param name: unique name given to the terraform block, for example: 'aws_vpc.example_name'
            :param config: the section in tf_definitions that belong to this block
            :param path: the file location of the block
            :param block_type: BlockType
            :param attributes: dictionary of the block's original attributes in the terraform file
        """
        self.name = name
        self.config = deepcopy(config)
        self.module_dependency = ""
        self.module_dependency_num = ""
        if path:
            path, module_dependency, num = remove_module_dependency_in_path(path)
            self.path = os.path.realpath(path)
            if module_dependency:
                self.module_dependency = module_dependency
                self.module_dependency_num = num
        else:
            self.path = path
        self.block_type = block_type
        if attributes.get(RESOLVED_MODULE_ENTRY_NAME):
            del attributes[RESOLVED_MODULE_ENTRY_NAME]
        self.attributes = attributes
        self.id = id
        self.source = source
        self.changed_attributes = {}
        self.breadcrumbs = {}
        self.module_connections = {}
        self.source_module = set()

        attributes_to_add = self._extract_inner_attributes()
        self.attributes.update(attributes_to_add)
        self.encode = encode

    def _extract_inner_attributes(self):
        attributes_to_add = {}
        for attribute_key in self.attributes:
            attribute_value = self.attributes[attribute_key]
            if type(attribute_value) is list and len(attribute_value) > 0 and type(attribute_value[0]) is dict:
                inner_attributes = get_inner_attributes(attribute_key, attribute_value)
                attributes_to_add.update(inner_attributes)
        return attributes_to_add

    def __str__(self):
        return str(self.block_type) + ': ' + self.name

    def get_attribute_dict(self):
        """
           :return: map of all the block's native attributes (from the source file),
           combined with the attributes generated by the module builder.
           If the attributes are not a primitive type, they are converted to strings.
           """
        base_attributes = self.get_base_attributes()
        self.get_origin_attributes(base_attributes)

        if self.changed_attributes:
            # add changed attributes only for calculating the hash
            changed_attributes_keys = list(self.changed_attributes.keys())
            changed_attributes_keys.sort()
            base_attributes['changed_attributes'] = changed_attributes_keys

        if self.breadcrumbs:
            sorted_breadcrumbs = dict(sorted(self.breadcrumbs.items()))
            base_attributes[CustomAttributes.RENDERING_BREADCRUMBS] = sorted_breadcrumbs

        if self.encode:
            for attribute in base_attributes:
                value_to_encode = base_attributes[attribute]
                encoded_value = utils.encode_graph_property_value(value_to_encode)
                base_attributes[attribute] = encoded_value

        base_attributes[CustomAttributes.HASH] = calculate_hash(base_attributes)

        if base_attributes.get('changed_attributes'):
            # removed changed attributes if it was added previously for calculating hash.
            del base_attributes['changed_attributes']

        return base_attributes

    def get_origin_attributes(self, base_attributes):
        for attribute_key in list(self.attributes.keys()):
            attribute_value = self.attributes[attribute_key]
            if type(attribute_value) is list and len(attribute_value) == 1:
                attribute_value = attribute_value[0]
            if type(attribute_value) is dict or type(attribute_value) is list:
                inner_attributes = get_inner_attributes(attribute_key, attribute_value)
                base_attributes.update(inner_attributes)
            if attribute_key == 'self':
                base_attributes['self_'] = attribute_value
                continue
            else:
                base_attributes[attribute_key] = attribute_value

    def get_hash(self):
        attributes_dict = self.get_attribute_dict()
        return attributes_dict.get(CustomAttributes.HASH)

    def get_decoded_attribute_dict(self):
        attributes = self.get_attribute_dict()
        if self.encode:
            for attribute_key in attributes:
                attributes[attribute_key] = decode_graph_property_value(attributes[attribute_key])
        return attributes

    def update_attribute(self, attribute_key, attribute_value, change_origin_id, previous_breadcrumbs):
        if not previous_breadcrumbs or previous_breadcrumbs[-1] != change_origin_id:
            previous_breadcrumbs.append(change_origin_id)

        self.update_inner_attribute(attribute_key, self.attributes, attribute_value)
        attribute_key_parts = attribute_key.split('.')
        if len(attribute_key_parts) == 1:
            self.changed_attributes[attribute_key] = previous_breadcrumbs
            return
        for i in range(len(attribute_key_parts)):
            key = join_trimmed_strings(char_to_join=".", str_lst=attribute_key_parts, num_to_trim=i)
            if key.find('.') > -1:
                self.attributes[key] = attribute_value
                attribute_value = {attribute_key_parts[len(attribute_key_parts)-1 - i]: attribute_value}
                self.changed_attributes[key] = previous_breadcrumbs

    def update_inner_attribute(self, attribute_key, nested_attributes, value_to_update):
        split_key = attribute_key.split('.')
        curr_key = split_key[0]
        if curr_key.isnumeric():
            curr_key = int(curr_key)
        if type(nested_attributes) is dict and nested_attributes.get(attribute_key):
            nested_attributes[attribute_key] = value_to_update
        if type(nested_attributes) is list and type(curr_key) is not int:
            for inner in nested_attributes:
                self.update_inner_attribute(curr_key, inner, value_to_update)
        elif len(split_key) == 1:
            nested_attributes[curr_key] = value_to_update
        else:
            try:
                self.update_inner_attribute('.'.join(split_key[1:]), nested_attributes[curr_key],
                                            value_to_update)
            except Exception as e:
                if nested_attributes.get(attribute_key) is not None:
                    nested_attributes[attribute_key] = value_to_update
                else:
                    logging.warning(f'unable to update inner attribute {attribute_key} because {e}')
                    return e

    def add_module_connection(self, attribute_key, vertex_id):
        if not self.module_connections.get(attribute_key):
            self.module_connections[attribute_key] = []
        self.module_connections[attribute_key].append(vertex_id)

    def find_attribute(self, attribute):
        """
        :param attribute: key to search in self.attribute
        The function searches for  attribute in self.attribute. It might not exist if the block is variable or output,
        or its search path might be different if its a resource.
        :return: the actual attribute key or None
        """
        if not attribute:
            return None

        if self.attributes.get(attribute[0]):
            return attribute[0]

        if self.block_type == BlockType.VARIABLE:
            return 'default' if self.attributes.get('default') else None

        if self.block_type == BlockType.OUTPUT:
            return 'value' if self.attributes.get('value') else None

        if self.block_type == BlockType.RESOURCE and len(attribute) > 1:
            # handle cases where attribute_at_dest == ['aws_s3_bucket.template_bucket', 'acl']
            if self.name == attribute[0] and self.attributes.get(attribute[1]):
                return attribute[1]

        return None

    def get_export_data(self):
        return {'type': self.block_type.value, 'name': self.name, 'path': self.path}

    def get_base_attributes(self):
        return {
            CustomAttributes.BLOCK_NAME: self.name,
            CustomAttributes.BLOCK_TYPE: self.block_type.value,
            CustomAttributes.FILE_PATH: self.path,
            CustomAttributes.CONFIG: self.config,
            CustomAttributes.LABEL: self.__str__(),
            CustomAttributes.ID: self.id,
            CustomAttributes.SOURCE: self.source
        }


def get_inner_attributes(attribute_key, attribute_value):
    inner_attributes = {}
    if type(attribute_value) is list and len(attribute_value) == 1:
        attribute_value = attribute_value[0]

    if type(attribute_value) in [dict, list]:
        inner_attributes[attribute_key] = [None] * len(attribute_value) if type(attribute_value) is list else {}
        iterator = range(len(attribute_value)) if type(attribute_value) is list else list(attribute_value.keys())
        for key in iterator:
            inner_key = f'{attribute_key}.{key}'
            inner_value = attribute_value[key]
            inner_attributes.update(get_inner_attributes(inner_key, inner_value))
            inner_attributes[attribute_key][key] = inner_attributes[inner_key]
    else:
        inner_attributes[attribute_key] = attribute_value
    return inner_attributes
