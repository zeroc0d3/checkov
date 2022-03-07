from copy import deepcopy
from typing import Union, Dict, Any, List, Optional

from checkov.common.graph.graph_builder.graph_components.attribute_names import CustomAttributes
from checkov.common.graph.graph_builder.utils import calculate_hash, join_trimmed_strings
from checkov.common.graph.graph_builder.variable_rendering.breadcrumb_metadata import BreadcrumbMetadata
from checkov.terraform.graph_builder.graph_components.block_types import BlockType


class Block:
    def __init__(
            self,
            name: str,
            config: Dict[str, Any],
            path: str,
            block_type: str,
            attributes: Dict[str, Any],
            id: str = "",
            source: str = "",
    ) -> None:
        """
            :param name: unique name given to the block, for example
            :param config: the section in tf_definitions that belong to this block
            :param path: the file location of the block
            :param block_type: str
            :param attributes: dictionary of the block's original attributes in the origin file
        """
        self.name = name
        self.config = deepcopy(config)
        self.path = path
        self.block_type = block_type
        self.attributes = attributes
        self.id = id
        self.source = source
        self.changed_attributes: Dict[str, List[Any]] = {}
        self.breadcrumbs: Dict[str, List[Dict[str, Any]]] = {}

        attributes_to_add = self._extract_inner_attributes()
        self.attributes.update(attributes_to_add)

    def _extract_inner_attributes(self) -> Dict[str, Any]:
        attributes_to_add = {}
        for attribute_key, attribute_value in self.attributes.items():
            if isinstance(attribute_value, dict) or (
                isinstance(attribute_value, list) and len(attribute_value) > 0 and isinstance(attribute_value[0], dict)
            ):
                inner_attributes = self.get_inner_attributes(
                    attribute_key=attribute_key,
                    attribute_value=attribute_value,
                )
                attributes_to_add.update(inner_attributes)
        return attributes_to_add

    def __str__(self) -> str:
        return f"{self.block_type}: {self.name}"

    def get_attribute_dict(self, add_hash=True) -> Dict[str, Any]:
        """
           :return: map of all the block's native attributes (from the source file),
           combined with the attributes generated by the module builder.
           If the attributes are not a primitive type, they are converted to strings.
           """
        base_attributes = self.get_base_attributes()
        self.get_origin_attributes(base_attributes)

        if hasattr(self, "module_dependency") and hasattr(self, "module_dependency_num"):
            base_attributes[CustomAttributes.MODULE_DEPENDENCY] = self.module_dependency
            base_attributes[CustomAttributes.MODULE_DEPENDENCY_NUM] = self.module_dependency_num

        if self.changed_attributes:
            # add changed attributes only for calculating the hash
            base_attributes["changed_attributes"] = sorted(self.changed_attributes.keys())

        if self.breadcrumbs:
            sorted_breadcrumbs = dict(sorted(self.breadcrumbs.items()))
            base_attributes[CustomAttributes.RENDERING_BREADCRUMBS] = sorted_breadcrumbs

        if add_hash:
            base_attributes[CustomAttributes.HASH] = calculate_hash(base_attributes)

        if self.block_type == BlockType.DATA:
            base_attributes[CustomAttributes.RESOURCE_TYPE] = f'data.{self.id.split(".")[0]}'

        if "changed_attributes" in base_attributes:
            # removed changed attributes if it was added previously for calculating hash.
            del base_attributes["changed_attributes"]

        return base_attributes

    def get_origin_attributes(self, base_attributes: Dict[str, Any]) -> None:
        for attribute_key in list(self.attributes.keys()):
            attribute_value = self.attributes[attribute_key]
            if isinstance(attribute_value, list) and len(attribute_value) == 1:
                attribute_value = attribute_value[0]
            # needs to be checked before adding anything to 'base_attributes'
            if attribute_key == "self":
                base_attributes["self_"] = attribute_value
                continue
            if isinstance(attribute_value, (list, dict)):
                inner_attributes = self.get_inner_attributes(attribute_key, attribute_value, False)
                base_attributes.update(inner_attributes)
            else:
                base_attributes[attribute_key] = attribute_value

    def get_hash(self) -> str:
        attributes_dict = self.get_attribute_dict()
        return attributes_dict.get(CustomAttributes.HASH, "")

    def update_attribute(
        self,
        attribute_key: str,
        attribute_value: Any,
        change_origin_id: Optional[int],
        previous_breadcrumbs: List[BreadcrumbMetadata],
        attribute_at_dest: Optional[str],
        transform_step: bool = False,
    ) -> None:
        if self._should_add_previous_breadcrumbs(change_origin_id, previous_breadcrumbs, attribute_at_dest):
            previous_breadcrumbs.append(BreadcrumbMetadata(change_origin_id, attribute_at_dest))

        # update the numbered attributes, if the new value is a list
        if isinstance(attribute_value, list):
            for idx, value in enumerate(attribute_value):
                self.attributes[f"{attribute_key}.{idx}"] = value

        attribute_key_parts = attribute_key.split(".")
        if len(attribute_key_parts) == 1:
            self.attributes[attribute_key] = attribute_value
            if self._should_set_changed_attributes(change_origin_id, attribute_at_dest):
                self.changed_attributes[attribute_key] = previous_breadcrumbs
            return
        for i in range(len(attribute_key_parts)):
            key = join_trimmed_strings(char_to_join=".", str_lst=attribute_key_parts, num_to_trim=i)
            if key.find(".") > -1:
                self.attributes[key] = attribute_value
                end_key_part = attribute_key_parts[len(attribute_key_parts) - 1 - i]
                if transform_step and end_key_part in ("1", "2"):
                    # if condition logic during the transform step breaks the values
                    return
                attribute_value = {end_key_part: attribute_value}
                if self._should_set_changed_attributes(change_origin_id, attribute_at_dest):
                    self.changed_attributes[key] = previous_breadcrumbs

    @staticmethod
    def _should_add_previous_breadcrumbs(change_origin_id: Optional[int],
            previous_breadcrumbs: List[BreadcrumbMetadata], attribute_at_dest: Optional[str]):
        return not previous_breadcrumbs or previous_breadcrumbs[-1].vertex_id != change_origin_id

    @staticmethod
    def _should_set_changed_attributes(change_origin_id: Optional[int], attribute_at_dest: Optional[str]):
        return True

    def get_export_data(self) -> Dict[str, Union[bool, str]]:
        return {"type": self.block_type, "name": self.name, "path": self.path}

    def get_base_attributes(self) -> Dict[str, Union[str, List[str], Dict[str, Any]]]:
        return {
            CustomAttributes.BLOCK_NAME: self.name,
            CustomAttributes.BLOCK_TYPE: self.block_type,
            CustomAttributes.FILE_PATH: self.path,
            CustomAttributes.CONFIG: self.config,
            CustomAttributes.LABEL: str(self),
            CustomAttributes.ID: self.id,
            CustomAttributes.SOURCE: self.source,
        }

    @classmethod
    def get_inner_attributes(
        cls,
        attribute_key: str,
        attribute_value: Union[str, List[str], Dict[str, Any]],
        strip_list: bool = True  # used by subclass
    ) -> Dict[str, Any]:
        inner_attributes: Dict[str, Any] = {}

        if isinstance(attribute_value, (dict, list)):
            inner_attributes[attribute_key] = [None] * len(attribute_value) if isinstance(attribute_value, list) else {}
            iterator: Union[range, List[str]] = range(len(attribute_value)) if isinstance(
                attribute_value, list
            ) else list(
                attribute_value.keys()
            )
            for key in iterator:
                if key != "":
                    inner_key = f"{attribute_key}.{key}"
                    inner_value = attribute_value[key]
                    inner_attributes.update(cls.get_inner_attributes(inner_key, inner_value))
                    inner_attributes[attribute_key][key] = inner_attributes[inner_key]
                else:
                    del attribute_value[key]
        else:
            inner_attributes[attribute_key] = attribute_value
        return inner_attributes
