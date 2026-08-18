"""Domain-stratified template definitions for toke corpus generation.

Defines 8 application domains (WEB, DATA, CRYPTO, FILE_IO, CONFIG, TEST,
NETWORK, CLI) with parameterized task templates, stdlib context, and
domain-specific parameter pools for deterministic expansion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SPEC_REFERENCE = (Path(__file__).parent.parent / "spec-reference.md").read_text()

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

VALID_TOKE_TYPES: set[str] = {
    "i64", "u64", "f64", "$str", "bool", "$void",
    "@(i64)", "@(u64)", "@(f64)", "@($str)", "@(bool)",
    "@($str:$str)", "@($str:i64)", "@($str:bool)", "@($str:f64)",
}


@dataclass(frozen=True)
class DomainTemplate:
    """A parameterized task template within a domain."""

    template_id: str
    description_template: str
    signature_template: str
    difficulty: int  # 1-5
    domain: str


# ---------------------------------------------------------------------------
# Domains enum
# ---------------------------------------------------------------------------

DOMAINS: list[str] = [
    "WEB", "DATA", "CRYPTO", "FILE_IO", "CONFIG", "TEST", "NETWORK", "CLI",
]


# ---------------------------------------------------------------------------
# Stdlib context per domain
# ---------------------------------------------------------------------------

STDLIB_CONTEXT: dict[str, str] = {
    "WEB": (
        "i=std.http;\n"
        "# std.http exports:\n"
        "#   f=get(url:$str):$str  -- HTTP GET, returns body\n"
        "#   f=post(url:$str;body:$str):$str  -- HTTP POST\n"
        "#   f=put(url:$str;body:$str):$str  -- HTTP PUT\n"
        "#   f=delete(url:$str):$str  -- HTTP DELETE\n"
        "i=std.json;\n"
        "# std.json exports:\n"
        "#   f=parse(s:$str):$str  -- parse JSON string\n"
        "#   f=stringify(s:$str):$str  -- convert to JSON string\n"
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=replace(s:$str;old:$str;new:$str):$str\n"
        "#   f=toupper(s:$str):$str\n"
        "#   f=tolower(s:$str):$str"
    ),
    "DATA": (
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=replace(s:$str;old:$str;new:$str):$str\n"
        "i=std.json;\n"
        "# std.json exports:\n"
        "#   f=parse(s:$str):$str\n"
        "#   f=stringify(s:$str):$str\n"
        "i=std.db;\n"
        "# std.db exports:\n"
        "#   f=open(dsn:$str):$str  -- open database connection\n"
        "#   f=query(conn:$str;sql:$str):@($str)  -- query rows\n"
        "#   f=execute(conn:$str;sql:$str):i64  -- execute statement"
    ),
    "CRYPTO": (
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=replace(s:$str;old:$str;new:$str):$str"
    ),
    "FILE_IO": (
        "i=std.file;\n"
        "# std.file exports:\n"
        "#   f=read(path:$str):$str  -- read entire file\n"
        "#   f=write(path:$str;data:$str):$void  -- write file\n"
        "#   f=append(path:$str;data:$str):$void  -- append to file\n"
        "#   f=exists(path:$str):bool  -- check file exists\n"
        "#   f=delete(path:$str):$void  -- delete file\n"
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool"
    ),
    "CONFIG": (
        "i=std.file;\n"
        "# std.file exports:\n"
        "#   f=read(path:$str):$str\n"
        "#   f=write(path:$str;data:$str):$void\n"
        "#   f=exists(path:$str):bool\n"
        "i=std.json;\n"
        "# std.json exports:\n"
        "#   f=parse(s:$str):$str\n"
        "#   f=stringify(s:$str):$str\n"
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=replace(s:$str;old:$str;new:$str):$str"
    ),
    "TEST": (
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=toupper(s:$str):$str\n"
        "#   f=tolower(s:$str):$str"
    ),
    "NETWORK": (
        "i=std.http;\n"
        "# std.http exports:\n"
        "#   f=get(url:$str):$str\n"
        "#   f=post(url:$str;body:$str):$str\n"
        "#   f=put(url:$str;body:$str):$str\n"
        "#   f=delete(url:$str):$str\n"
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=trim(s:$str):$str"
    ),
    "CLI": (
        "i=std.str;\n"
        "# std.str exports:\n"
        "#   f=split(s:$str;delim:$str):@($str)\n"
        "#   f=join(parts:@($str);delim:$str):$str\n"
        "#   f=trim(s:$str):$str\n"
        "#   f=contains(s:$str;sub:$str):bool\n"
        "#   f=replace(s:$str;old:$str;new:$str):$str\n"
        "#   f=toupper(s:$str):$str\n"
        "#   f=tolower(s:$str):$str\n"
        "i=std.file;\n"
        "# std.file exports:\n"
        "#   f=read(path:$str):$str\n"
        "#   f=write(path:$str;data:$str):$void\n"
        "#   f=exists(path:$str):bool"
    ),
}


# ---------------------------------------------------------------------------
# Parameter pools per domain
# ---------------------------------------------------------------------------

PARAM_POOLS: dict[str, dict[str, list[str]]] = {
    "WEB": {
        "resource": ["user", "post", "comment", "product", "order",
                      "session", "token", "profile", "message", "event"],
        "http_method": ["get", "post", "put", "delete"],
        "status_code": ["200", "201", "204", "301", "400", "401", "403",
                        "404", "500"],
        "content_type": ["json", "html", "xml", "text", "csv"],
        "header_name": ["contenttype", "authorization", "accept",
                        "cachecontrol", "host", "origin"],
        "path_segment": ["api", "v1", "v2", "users", "items", "auth",
                         "search", "admin", "public", "health"],
    },
    "DATA": {
        "field_name": ["name", "age", "email", "score", "count", "total",
                       "price", "weight", "height", "rating"],
        "agg_op": ["sum", "avg", "min", "max", "count"],
        "sort_order": ["asc", "desc"],
        "data_format": ["csv", "json", "tsv"],
        "delimiter": [",", ";", "|", "\t"],
        "column_type": ["i64", "f64", "$str", "bool"],
    },
    "CRYPTO": {
        "algo_name": ["caesar", "xor", "rot13", "base64", "vigenere",
                      "atbash", "railfence", "substitution", "reverse",
                      "shift"],
        "shift_amount": ["1", "3", "5", "7", "13", "16", "25"],
        "encoding": ["hex", "base64", "binary", "octal", "decimal"],
        "hash_property": ["length", "checksum", "parity", "modsum",
                          "xorsum"],
        "bit_op": ["xor", "and", "or", "shift", "rotate"],
    },
    "FILE_IO": {
        "file_ext": ["txt", "csv", "log", "dat", "cfg", "ini", "tmp"],
        "operation": ["read", "write", "append", "copy", "merge",
                      "filter", "count", "search", "replace", "split"],
        "line_ending": ["lf", "crlf"],
        "encoding_name": ["utf8", "ascii"],
        "path_part": ["dir", "file", "ext", "base", "parent"],
    },
    "CONFIG": {
        "config_key": ["host", "port", "timeout", "retries", "debug",
                       "loglevel", "maxconn", "workers", "cachettl",
                       "batchsize"],
        "config_format": ["json", "ini", "env", "toml", "yaml"],
        "env_var": ["appenv", "dbhost", "dbport", "apikey", "loglevel",
                    "workers", "timeout", "region", "version", "debug"],
        "default_val": ["0", "8080", "30", "3", "false", "info",
                        "100", "4", "60", "10"],
        "section": ["server", "database", "cache", "logging", "auth",
                    "api", "worker", "queue", "storage", "metrics"],
    },
    "TEST": {
        "assertion": ["eq", "neq", "gt", "lt", "gte", "lte", "contains",
                      "empty", "notempty", "true", "false"],
        "test_target": ["function", "module", "handler", "parser",
                        "validator", "formatter", "converter",
                        "calculator", "filter", "sorter"],
        "fixture_type": ["string", "number", "array", "map", "empty",
                         "large", "edge", "null", "nested", "mixed"],
        "outcome": ["pass", "fail", "skip", "error", "timeout"],
    },
    "NETWORK": {
        "protocol": ["http", "https", "tcp", "udp", "ws", "dns"],
        "ip_part": ["octet", "port", "mask", "cidr", "subnet"],
        "dns_record": ["a", "aaaa", "cname", "mx", "txt", "ns"],
        "net_metric": ["latency", "bandwidth", "packetloss", "jitter",
                       "throughput", "uptime"],
        "port_range": ["80", "443", "8080", "3000", "5432", "6379",
                       "27017", "9090", "8443", "22"],
    },
    "CLI": {
        "flag_name": ["verbose", "quiet", "output", "input", "help",
                      "version", "force", "dryrun", "config", "debug"],
        "arg_type": ["string", "number", "bool", "path", "list"],
        "exit_code": ["0", "1", "2", "126", "127", "128", "130"],
        "command": ["run", "build", "test", "lint", "fmt", "check",
                    "deploy", "init", "clean", "watch"],
        "output_format": ["text", "json", "csv", "table", "yaml"],
    },
}


# ---------------------------------------------------------------------------
# Template definitions per domain (10-20 per domain)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, list[DomainTemplate]] = {
    "WEB": [
        DomainTemplate("web001",
                       "Parse the query parameter '{param_key}' from URL query string",
                       "f=parsequery(qs:$str;key:$str):$str", 2, "WEB"),
        DomainTemplate("web002",
                       "Count the number of query parameters in a URL query string",
                       "f=countparams(qs:$str):i64", 1, "WEB"),
        DomainTemplate("web003",
                       "Build an HTTP {http_method} response with status code {status_code}",
                       "f=buildresp(status:i64;body:$str):$str", 2, "WEB"),
        DomainTemplate("web004",
                       "Extract the path segment at index N from URL path",
                       "f=pathseg(path:$str;idx:i64):$str", 2, "WEB"),
        DomainTemplate("web005",
                       "Validate that a URL path starts with /{path_segment}",
                       "f=validpath(path:$str):bool", 1, "WEB"),
        DomainTemplate("web006",
                       "Build an HTTP header line for '{header_name}' with given value",
                       "f=buildheader(name:$str;val:$str):$str", 1, "WEB"),
        DomainTemplate("web007",
                       "Determine the content type string for '{content_type}' format",
                       "f=contenttype(ext:$str):$str", 2, "WEB"),
        DomainTemplate("web008",
                       "Check if HTTP status code {status_code} indicates success (2xx)",
                       "f=issuccess(code:i64):bool", 1, "WEB"),
        DomainTemplate("web009",
                       "Route a request to /{path_segment}/{resource} endpoint handler",
                       "f=route(method:$str;path:$str):$str", 3, "WEB"),
        DomainTemplate("web010",
                       "Join two URL path segments avoiding double slashes",
                       "f=joinpath(a:$str;b:$str):$str", 2, "WEB"),
        DomainTemplate("web011",
                       "Extract the HTTP method from a raw request line",
                       "f=extractmethod(line:$str):$str", 2, "WEB"),
        DomainTemplate("web012",
                       "Build a JSON error response body with status and message",
                       "f=jsonerr(status:i64;msg:$str):$str", 2, "WEB"),
        DomainTemplate("web013",
                       "Normalize a URL path by removing trailing slashes",
                       "f=normpath(path:$str):$str", 2, "WEB"),
        DomainTemplate("web014",
                       "Check if a request path matches the pattern /{path_segment}/:id",
                       "f=matchroute(path:$str;pattern:$str):bool", 3, "WEB"),
        DomainTemplate("web015",
                       "Encode a {resource} name for safe use in a URL query string",
                       "f=urlencode(s:$str):$str", 3, "WEB"),
    ],
    "DATA": [
        DomainTemplate("dat001",
                       "Compute the {agg_op} of an array of {column_type} values",
                       "f=aggregate(vals:@(f64)):f64", 2, "DATA"),
        DomainTemplate("dat002",
                       "Filter records where '{field_name}' exceeds a threshold",
                       "f=filtergt(vals:@(i64);threshold:i64):@(i64)", 2, "DATA"),
        DomainTemplate("dat003",
                       "Parse a {data_format} row into an array of field values",
                       "f=parserow(line:$str;delim:$str):@($str)", 2, "DATA"),
        DomainTemplate("dat004",
                       "Sort an array of i64 values in {sort_order} order",
                       "f=sortvals(vals:@(i64)):@(i64)", 3, "DATA"),
        DomainTemplate("dat005",
                       "Count occurrences of a value in an array of {column_type}",
                       "f=countval(vals:@($str);target:$str):i64", 2, "DATA"),
        DomainTemplate("dat006",
                       "Find the index of the maximum value in an array",
                       "f=maxidx(vals:@(i64)):i64", 2, "DATA"),
        DomainTemplate("dat007",
                       "Compute the running {agg_op} of an array of f64",
                       "f=running(vals:@(f64)):@(f64)", 3, "DATA"),
        DomainTemplate("dat008",
                       "Merge two sorted arrays of i64 into a single sorted array",
                       "f=mergesorted(a:@(i64);b:@(i64)):@(i64)", 3, "DATA"),
        DomainTemplate("dat009",
                       "Remove duplicate values from an array of {column_type}",
                       "f=dedup(vals:@($str)):@($str)", 3, "DATA"),
        DomainTemplate("dat010",
                       "Build a frequency map from an array of string values",
                       "f=freqmap(vals:@($str)):@($str:i64)", 3, "DATA"),
        DomainTemplate("dat011",
                       "Compute the median of an array of f64 values",
                       "f=median(vals:@(f64)):f64", 3, "DATA"),
        DomainTemplate("dat012",
                       "Pivot rows by grouping on the '{field_name}' field",
                       "f=groupby(rows:@($str);keyidx:i64):@($str:$str)", 4, "DATA"),
        DomainTemplate("dat013",
                       "Flatten a nested {data_format} structure into key-value pairs",
                       "f=flatten(data:$str):@($str)", 3, "DATA"),
        DomainTemplate("dat014",
                       "Validate that all values in column are valid {column_type}",
                       "f=validatecol(vals:@($str)):bool", 2, "DATA"),
        DomainTemplate("dat015",
                       "Compute the standard deviation of an array of f64 values",
                       "f=stddev(vals:@(f64)):f64", 3, "DATA"),
    ],
    "CRYPTO": [
        DomainTemplate("cry001",
                       "Encrypt a string using {algo_name} cipher with shift {shift_amount}",
                       "f=encrypt(plaintext:$str;shift:i64):$str", 2, "CRYPTO"),
        DomainTemplate("cry002",
                       "Decrypt a string using {algo_name} cipher with shift {shift_amount}",
                       "f=decrypt(ciphertext:$str;shift:i64):$str", 2, "CRYPTO"),
        DomainTemplate("cry003",
                       "Compute a simple checksum of a string using {bit_op} operation",
                       "f=checksum(data:$str):i64", 2, "CRYPTO"),
        DomainTemplate("cry004",
                       "Encode a string to {encoding} representation",
                       "f=encode(data:$str):$str", 3, "CRYPTO"),
        DomainTemplate("cry005",
                       "Decode a {encoding} encoded string back to plain text",
                       "f=decode(encoded:$str):$str", 3, "CRYPTO"),
        DomainTemplate("cry006",
                       "Generate a simple hash of a string returning i64 {hash_property}",
                       "f=simplehash(data:$str):i64", 2, "CRYPTO"),
        DomainTemplate("cry007",
                       "XOR two strings of equal length byte by byte",
                       "f=xorstrings(a:$str;b:$str):$str", 3, "CRYPTO"),
        DomainTemplate("cry008",
                       "Rotate each character in a string by the given amount",
                       "f=rotate(text:$str;amount:i64):$str", 2, "CRYPTO"),
        DomainTemplate("cry009",
                       "Verify that a {hash_property} matches the expected value for data",
                       "f=verifyhash(data:$str;expected:i64):bool", 2, "CRYPTO"),
        DomainTemplate("cry010",
                       "Apply a substitution cipher using a key mapping string",
                       "f=substitute(text:$str;key:$str):$str", 3, "CRYPTO"),
        DomainTemplate("cry011",
                       "Compute the {bit_op} of all byte values in a string",
                       "f=bitreduce(data:$str):i64", 2, "CRYPTO"),
        DomainTemplate("cry012",
                       "Convert a string to its {encoding} digit representation",
                       "f=todigits(data:$str):@(i64)", 3, "CRYPTO"),
    ],
    "FILE_IO": [
        DomainTemplate("fio001",
                       "Read a {file_ext} file and return its contents as a string",
                       "f=readfile(path:$str):$str", 1, "FILE_IO"),
        DomainTemplate("fio002",
                       "Write data to a {file_ext} file, overwriting existing content",
                       "f=writefile(path:$str;data:$str):$void", 1, "FILE_IO"),
        DomainTemplate("fio003",
                       "Append a line to a {file_ext} file",
                       "f=appendline(path:$str;line:$str):$void", 1, "FILE_IO"),
        DomainTemplate("fio004",
                       "Count the number of lines in a {file_ext} file",
                       "f=countlines(path:$str):i64", 2, "FILE_IO"),
        DomainTemplate("fio005",
                       "Search for a pattern in a file and return matching lines",
                       "f=grepfile(path:$str;pattern:$str):@($str)", 3, "FILE_IO"),
        DomainTemplate("fio006",
                       "Copy contents from source {file_ext} file to destination",
                       "f=copyfile(src:$str;dst:$str):$void", 2, "FILE_IO"),
        DomainTemplate("fio007",
                       "Extract the {path_part} component from a file path",
                       "f=pathpart(path:$str):$str", 2, "FILE_IO"),
        DomainTemplate("fio008",
                       "Merge two {file_ext} files line by line into a single output",
                       "f=mergefiles(a:$str;b:$str):$str", 3, "FILE_IO"),
        DomainTemplate("fio009",
                       "Filter lines in a file keeping only those containing a substring",
                       "f=filterlines(path:$str;sub:$str):@($str)", 3, "FILE_IO"),
        DomainTemplate("fio010",
                       "Replace all occurrences of old text with new text in a file",
                       "f=replaceinfile(path:$str;old:$str;new:$str):$void", 3, "FILE_IO"),
        DomainTemplate("fio011",
                       "Check whether a file at the given path exists",
                       "f=fileexists(path:$str):bool", 1, "FILE_IO"),
        DomainTemplate("fio012",
                       "Read a {file_ext} file and split into an array of lines",
                       "f=readlines(path:$str):@($str)", 2, "FILE_IO"),
        DomainTemplate("fio013",
                       "Write an array of strings as lines to a {file_ext} file",
                       "f=writelines(path:$str;lines:@($str)):$void", 2, "FILE_IO"),
    ],
    "CONFIG": [
        DomainTemplate("cfg001",
                       "Read the value of config key '{config_key}' from a {config_format} string",
                       "f=getconfig(data:$str;key:$str):$str", 2, "CONFIG"),
        DomainTemplate("cfg002",
                       "Set the value of config key '{config_key}' in a {config_format} string",
                       "f=setconfig(data:$str;key:$str;val:$str):$str", 3, "CONFIG"),
        DomainTemplate("cfg003",
                       "Check if config key '{config_key}' exists in config data",
                       "f=hasconfig(data:$str;key:$str):bool", 2, "CONFIG"),
        DomainTemplate("cfg004",
                       "Read environment variable '{env_var}' with default value '{default_val}'",
                       "f=getenv(envdata:$str;key:$str;def:$str):$str", 2, "CONFIG"),
        DomainTemplate("cfg005",
                       "Parse a {config_format} config file into a map of key-value pairs",
                       "f=parseconfig(data:$str):@($str:$str)", 3, "CONFIG"),
        DomainTemplate("cfg006",
                       "Merge two config maps giving priority to the override map",
                       "f=mergeconfig(base:@($str:$str);over:@($str:$str)):@($str:$str)", 4, "CONFIG"),
        DomainTemplate("cfg007",
                       "Validate that required config key '{config_key}' is present and non-empty",
                       "f=requireconfig(data:$str;key:$str):bool", 2, "CONFIG"),
        DomainTemplate("cfg008",
                       "Extract all keys from a {config_format} config section '{section}'",
                       "f=sectionkeys(data:$str;section:$str):@($str)", 3, "CONFIG"),
        DomainTemplate("cfg009",
                       "Convert a flat key-value config map to {config_format} format string",
                       "f=serialize(cfg:@($str:$str)):$str", 3, "CONFIG"),
        DomainTemplate("cfg010",
                       "Read a config integer value for '{config_key}' with default {default_val}",
                       "f=getint(data:$str;key:$str;def:i64):i64", 2, "CONFIG"),
        DomainTemplate("cfg011",
                       "List all config sections in a {config_format} data string",
                       "f=listsections(data:$str):@($str)", 3, "CONFIG"),
        DomainTemplate("cfg012",
                       "Remove a config key '{config_key}' from config data",
                       "f=removekey(data:$str;key:$str):$str", 3, "CONFIG"),
    ],
    "TEST": [
        DomainTemplate("tst001",
                       "Assert that two {column_type} values are equal and return result",
                       "f=asserteq(a:$str;b:$str):bool", 1, "TEST"),
        DomainTemplate("tst002",
                       "Run a {test_target} test case and return {outcome} or pass",
                       "f=runtest(name:$str;expected:$str;actual:$str):$str", 2, "TEST"),
        DomainTemplate("tst003",
                       "Build a test fixture of type '{fixture_type}' for the test suite",
                       "f=fixture(kind:$str):$str", 2, "TEST"),
        DomainTemplate("tst004",
                       "Count the number of passing tests from an array of results",
                       "f=countpass(results:@($str)):i64", 2, "TEST"),
        DomainTemplate("tst005",
                       "Format a test result with name, status, and duration",
                       "f=fmtresult(name:$str;status:$str;ms:i64):$str", 2, "TEST"),
        DomainTemplate("tst006",
                       "Assert that a value is greater than a threshold",
                       "f=assertgt(actual:i64;threshold:i64):bool", 1, "TEST"),
        DomainTemplate("tst007",
                       "Generate a test report summary from pass and fail counts",
                       "f=report(pass:i64;fail:i64):$str", 2, "TEST"),
        DomainTemplate("tst008",
                       "Assert that a string contains the expected substring",
                       "f=assertcontains(haystack:$str;needle:$str):bool", 1, "TEST"),
        DomainTemplate("tst009",
                       "Build an array of {fixture_type} test fixtures for batch testing",
                       "f=batchfixtures(count:i64):@($str)", 3, "TEST"),
        DomainTemplate("tst010",
                       "Validate that a {test_target} produces the expected output type",
                       "f=validateoutput(expected:$str;actual:$str):bool", 2, "TEST"),
        DomainTemplate("tst011",
                       "Calculate the test pass rate from results array",
                       "f=passrate(results:@($str)):f64", 2, "TEST"),
        DomainTemplate("tst012",
                       "Filter test results keeping only those with {outcome} status",
                       "f=filterresults(results:@($str);status:$str):@($str)", 3, "TEST"),
    ],
    "NETWORK": [
        DomainTemplate("net001",
                       "Parse an IP address string into its four octets",
                       "f=parseip(addr:$str):@(i64)", 2, "NETWORK"),
        DomainTemplate("net002",
                       "Validate that a string is a valid IPv4 address",
                       "f=validip(addr:$str):bool", 2, "NETWORK"),
        DomainTemplate("net003",
                       "Extract the port number from a host:port string",
                       "f=extractport(hostport:$str):i64", 2, "NETWORK"),
        DomainTemplate("net004",
                       "Build a {protocol} URL from host, port, and path components",
                       "f=buildurl(proto:$str;host:$str;port:i64;path:$str):$str", 3, "NETWORK"),
        DomainTemplate("net005",
                       "Check if a port number {port_range} is in the well-known range",
                       "f=iswellknown(port:i64):bool", 1, "NETWORK"),
        DomainTemplate("net006",
                       "Compute a CIDR subnet mask from a prefix length",
                       "f=cidrmask(prefix:i64):$str", 3, "NETWORK"),
        DomainTemplate("net007",
                       "Check if two IP addresses are in the same /{ip_part} subnet",
                       "f=samesubnet(a:$str;b:$str;mask:$str):bool", 4, "NETWORK"),
        DomainTemplate("net008",
                       "Format a {net_metric} measurement with value and unit",
                       "f=fmtmetric(name:$str;val:f64;unit:$str):$str", 2, "NETWORK"),
        DomainTemplate("net009",
                       "Parse a {protocol} URL and extract the host component",
                       "f=extracthost(url:$str):$str", 2, "NETWORK"),
        DomainTemplate("net010",
                       "Calculate average {net_metric} from an array of measurements",
                       "f=avgmetric(vals:@(f64)):f64", 2, "NETWORK"),
        DomainTemplate("net011",
                       "Build a {dns_record} DNS record string from name and value",
                       "f=dnsrecord(rtype:$str;name:$str;val:$str):$str", 2, "NETWORK"),
        DomainTemplate("net012",
                       "Validate that a port number is within the valid range 1-65535",
                       "f=validport(port:i64):bool", 1, "NETWORK"),
    ],
    "CLI": [
        DomainTemplate("cli001",
                       "Parse a --{flag_name} flag value from a command line args string",
                       "f=parseflag(args:$str;flag:$str):$str", 2, "CLI"),
        DomainTemplate("cli002",
                       "Check if a boolean flag --{flag_name} is present in args",
                       "f=hasflag(args:$str;flag:$str):bool", 2, "CLI"),
        DomainTemplate("cli003",
                       "Build a usage help string for a {command} command",
                       "f=usage(cmd:$str;desc:$str):$str", 2, "CLI"),
        DomainTemplate("cli004",
                       "Parse positional arguments from a command line string",
                       "f=positional(args:$str):@($str)", 2, "CLI"),
        DomainTemplate("cli005",
                       "Format an error message for exit code {exit_code}",
                       "f=fmterror(code:i64;msg:$str):$str", 2, "CLI"),
        DomainTemplate("cli006",
                       "Validate that required arguments are present for {command} command",
                       "f=validateargs(args:@($str);required:@($str)):bool", 3, "CLI"),
        DomainTemplate("cli007",
                       "Build a {output_format} formatted output table from rows",
                       "f=fmttable(rows:@($str);fmt:$str):$str", 3, "CLI"),
        DomainTemplate("cli008",
                       "Parse a key=value pair from a command line argument",
                       "f=parsekv(arg:$str):@($str)", 2, "CLI"),
        DomainTemplate("cli009",
                       "Merge default config with command line overrides for {command}",
                       "f=mergeargs(defaults:@($str:$str);args:@($str:$str)):@($str:$str)", 4, "CLI"),
        DomainTemplate("cli010",
                       "Count the number of flags in a command line args string",
                       "f=countflags(args:$str):i64", 2, "CLI"),
        DomainTemplate("cli011",
                       "Build a progress bar string of given width and percentage",
                       "f=progressbar(pct:i64;width:i64):$str", 2, "CLI"),
        DomainTemplate("cli012",
                       "Format a duration in milliseconds to human-readable string",
                       "f=fmtduration(ms:i64):$str", 2, "CLI"),
    ],
}


def get_templates(domain: str) -> list[DomainTemplate]:
    """Return templates for a domain, or raise KeyError."""
    if domain not in _TEMPLATES:
        raise KeyError(f"Unknown domain: {domain!r}")
    return list(_TEMPLATES[domain])


def get_all_templates() -> dict[str, list[DomainTemplate]]:
    """Return a copy of all domain templates."""
    return {d: list(ts) for d, ts in _TEMPLATES.items()}


def get_stdlib_context(domain: str) -> str:
    """Return stdlib context for a domain."""
    if domain not in STDLIB_CONTEXT:
        raise KeyError(f"Unknown domain: {domain!r}")
    return STDLIB_CONTEXT[domain]


def get_param_pool(domain: str) -> dict[str, list[str]]:
    """Return the parameter pool for a domain."""
    if domain not in PARAM_POOLS:
        raise KeyError(f"Unknown domain: {domain!r}")
    return dict(PARAM_POOLS[domain])
