#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the actual test-command uint32 parser against Python integers."""

import argparse
import ctypes
import hashlib
import json
import pathlib
import random
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    source = pathlib.Path(__file__).resolve().parents[1] / "encfs_test_main.c"
    text = source.read_text()
    function = re.search(r"static bool parse_master_id\(.*?^}", text,
                         flags=re.MULTILINE | re.DOTALL)
    assert function is not None
    cases = [None, b"", b"0", b"1", b"4294967295", b"4294967296",
             b"18446744073709551615", b"18446744073709551616", b"-1", b"+1",
             b" 1", b"1 ", b"\t1", b"1\n", b"1\r", b"0x1", b"0Xff", b"1e3",
             b"1.0", b"01", b"00", b"00000000004294967295", b"1_000",
             "１２３".encode(), "١٢٣".encode(), b"\xff", b"0" * 128]
    rng = random.Random(0xFFFFFFFF)
    for unused in range(10000):
        value = rng.randrange(1 << 48)
        cases.append(str(value).encode())
        value = rng.randrange(1 << 32)
        cases.append(str(value).encode())
    for unused in range(10000):
        cases.append(bytes(rng.choice(b"0123456789 +-\t\n.xabcdef")
                           for index in range(rng.randrange(40))))

    checks = 0
    with tempfile.TemporaryDirectory(prefix="encfs-rekey-cli-") as folder:
        tmp = pathlib.Path(folder)
        wrapper = tmp / "parser.c"
        wrapper.write_text(
            "#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n" +
            function.group() + "\nint parse(const char *p, uint32_t *out)\n"
            "{ return parse_master_id(p, out); }\n")
        library = tmp / "parser.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra",
                        "-Werror", "-fsanitize=undefined",
                        "-fno-sanitize-recover=all", str(wrapper), "-o",
                        str(library)], check=True)
        lib = ctypes.CDLL(str(library))
        lib.parse.argtypes = (ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32))
        for raw in cases:
            expected = (raw is not None and bool(raw) and raw.isdigit() and
                        int(raw) <= 0xFFFFFFFF)
            out = ctypes.c_uint32(0x55AA55AA)
            result = bool(lib.parse(raw, ctypes.byref(out)))
            assert result == expected, repr(raw)
            assert out.value == (int(raw) if expected else 0x55AA55AA), repr(raw)
            checks += 2

    assert source.read_text() == text, "source changed during test"
    report = {"status": "passed", "checks": checks, "inputs": len(cases),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "parser_sha256": hashlib.sha256(function.group().encode()).hexdigest(),
              "oracle": "Python arbitrary-precision decimal integer comparison",
              "sanitizer": "undefined behavior; fatal on first diagnostic"}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
