import collections
import os
import re
import psutil

def dump_catalog():
    server_proc = None
    for p in psutil.process_iter(['name', 'exe']):
        if p.info['name'] == 'language_server.exe':
            server_proc = p
            break

    if not server_proc:
        raise RuntimeError("language_server.exe is not running.")

    exe_path = server_proc.info['exe']
    with open(exe_path, 'rb') as f:
        data = f.read()

    # Regex search for Connect-RPC / gRPC endpoint paths: /exa.<package>.<Service>/<Method>
    matches = re.findall(rb'/exa\.([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)/([a-zA-Z0-9_]+)', data)
    services = collections.defaultdict(set)
    for pkg, svc, method in matches:
        services[f"exa.{pkg.decode('ascii')}.{svc.decode('ascii')}"].add(method.decode('ascii'))

    total_methods = sum(len(m) for m in services.values())
    clean_exe_path = exe_path
    user_home = os.path.expanduser("~")
    if user_home in clean_exe_path:
        clean_exe_path = clean_exe_path.replace(user_home, "%USERPROFILE%")
    output_lines = [
        "# Antigravity Language Server API Catalog",
        "",
        f"> **Total Registered Services:** {len(services)}  ",
        f"> **Total Connect-RPC Endpoints:** {total_methods}  ",
        f"> **Binary Location:** `{clean_exe_path}`  ",
        "",
        "Every endpoint in this catalog is exposed locally over HTTPS on port `57071` via the Connect-RPC protocol.",
        "Headers required for calls:",
        "```http",
        "Content-Type: application/grpc-web+json",
        "x-codeium-csrf-token: <csrf_token>",
        "x-grpc-web: 1",
        "```",
        "",
        "---",
        ""
    ]

    for svc in sorted(services.keys()):
        methods = sorted(services[svc])
        output_lines.append(f"## `{svc}` ({len(methods)} methods)")
        output_lines.append("")
        output_lines.append("| # | RPC Method Name | Endpoint URI |")
        output_lines.append("| :--- | :--- | :--- |")
        for i, m in enumerate(methods, 1):
            endpoint = f"/{svc}/{m}"
            output_lines.append(f"| {i} | `{m}` | `https://127.0.0.1:57071{endpoint}` |")
        output_lines.append("")

    docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
    os.makedirs(docs_dir, exist_ok=True)
    out_file = os.path.join(docs_dir, "API_CATALOG.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"[+] Catalog successfully generated: {out_file}")
    print(f"[+] {len(services)} services, {total_methods} methods cataloged.")

if __name__ == "__main__":
    dump_catalog()
