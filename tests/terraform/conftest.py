"""Session-scoped parse of every .tf file under infra/terraform/, with no AWS credentials, no
network, and no terraform binary required — see CLAUDE.md § 8. Runs against source text via
python-hcl2, not a resolved plan; the one check that needs resolved values (the CIDR
`validation` block actually rejecting a bad value) lives in live verification, not here.
"""

from pathlib import Path

import hcl2
import pytest

TERRAFORM_DIR = Path(__file__).resolve().parents[2] / "infra" / "terraform"

_META_KEYS = {"__is_block__", "__comments__", "__start_line__", "__end_line__"}


def unwrap(value):
    """Strip the literal quote characters python-hcl2 leaves on plain string literals.

    python-hcl2 (this version) returns plain string literals *with* their source quotes intact
    (e.g. `type = "gp3"` parses to the Python string `'"gp3"'`) and returns interpolated
    expressions as `${...}` strings. This only strips a plain-literal wrapper; `${...}`
    expressions and non-strings pass through unchanged.
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def is_var_ref(value, var_name):
    """True if the raw HCL value is exactly a reference to var.<var_name>, e.g. '${var.foo}'."""
    return value == f"${{var.{var_name}}}"


def block(attrs, key):
    """Return a nested block (lifecycle { ... }, metadata_options { ... }, ...) as a plain dict,
    or None if the block is absent. Nested blocks parse as a one-element list containing a dict
    with bookkeeping keys mixed in; this strips those out.
    """
    value = attrs.get(key)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return {k: v for k, v in value[0].items() if k not in _META_KEYS}
    return None


class ParsedConfig:
    def __init__(self, files):
        self.files = files
        self.resources = {}
        self.data_sources = {}
        self.variables = {}
        self.outputs = {}
        self._ingest()

    def _ingest(self):
        for parsed in self.files.values():
            for resource_block in parsed.get("resource", []):
                for type_key, named in resource_block.items():
                    rtype = unwrap(type_key)
                    for name_key, attrs in named.items():
                        self.resources[(rtype, unwrap(name_key))] = attrs
            for data_block in parsed.get("data", []):
                for type_key, named in data_block.items():
                    dtype = unwrap(type_key)
                    for name_key, attrs in named.items():
                        self.data_sources[(dtype, unwrap(name_key))] = attrs
            for var_block in parsed.get("variable", []):
                for name_key, attrs in var_block.items():
                    self.variables[unwrap(name_key)] = attrs
            for out_block in parsed.get("output", []):
                for name_key, attrs in out_block.items():
                    self.outputs[unwrap(name_key)] = attrs

    def resources_of_type(self, rtype):
        return {name: attrs for (t, name), attrs in self.resources.items() if t == rtype}

    def data_sources_of_type(self, dtype):
        return {name: attrs for (t, name), attrs in self.data_sources.items() if t == dtype}


@pytest.fixture(scope="session")
def tf():
    files = {}
    tf_files = sorted(TERRAFORM_DIR.glob("*.tf"))
    assert tf_files, f"no .tf files found under {TERRAFORM_DIR}"
    for path in tf_files:
        with path.open() as f:
            files[path.name] = hcl2.load(f)
    return ParsedConfig(files)
