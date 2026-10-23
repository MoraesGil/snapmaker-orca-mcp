"""Single synchronization owner for ElementTree namespace serialization."""
from threading import RLock
from xml.etree import ElementTree as ET
from ...domain.types.json import json_object

XML_LOCK = RLock()


def native_xml(tree: ET.Element, core: str, production: str) -> bytes:
    """Restore the shared registry after native input preparation."""
    with XML_LOCK:
        registry = json_object(getattr(ET, '_namespace_map'), 'XML namespace registry')
        previous = dict(registry)
        try:
            ET.register_namespace('', core)
            ET.register_namespace('p', production)
            return ET.tostring(tree, encoding='unicode', xml_declaration=True).encode('utf-8')
        finally:
            registry.clear()
            registry.update(previous)
