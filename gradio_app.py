import gradio as gr
import pandas as pd
from packaging import specifiers, version
import re
import requests
import os
import tempfile
from uuid import uuid4

def parse_requirements(file_content):
    """Parse a requirements.txt content and return a dict of package names to specifier sets."""
    dependencies = {}
    for line in file_content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            match = re.match(r'^([a-zA-Z0-9_-]+)(.*)', line)
            if match:
                pkg_name = match.group(1).lower()
                spec = match.group(2).strip()
                if spec:
                    try:
                        spec_set = specifiers.SpecifierSet(spec)
                        dependencies[pkg_name] = spec_set
                    except specifiers.InvalidSpecifier:
                        return None, f"Invalid specifier for {pkg_name}: {spec}"
                else:
                    dependencies[pkg_name] = specifiers.SpecifierSet("")  # No version constraint
    return dependencies, None

def merge_specifiers(spec1, spec2):
    """Merge two specifier sets and return the intersection or None if incompatible."""
    try:
        merged = spec1 & spec2
        if not merged:
            return None
        return merged
    except specifiers.InvalidSpecifier:
        return None

def get_available_versions(package_name):
    """Fetch available versions for a package from PyPI."""
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        versions = [version.parse(v) for v in data['releases'].keys()]
        return sorted(versions, reverse=True)  # Sort descending
    except (requests.RequestException, KeyError):
        return []

def select_version(spec_set, package_name):
    """Select the most suitable version within the specifier set."""
    available_versions = get_available_versions(package_name)
    if not available_versions:
        return None, "No versions available"

    # Try largest version first
    for ver in available_versions:
        if str(ver) in spec_set:
            return ver, f"Selected largest version: {ver}"

    # Try smallest version
    for ver in sorted(available_versions):
        if str(ver) in spec_set:
            return ver, f"No larger version found; selected smallest version: {ver}"

    return None, "No compatible version found in available versions"

def analyze_requirements(file1_content, file2_content):
    """Analyze dependencies from two requirements.txt contents."""
    deps1, error1 = parse_requirements(file1_content)
    deps2, error2 = parse_requirements(file2_content)

    if error1 or error2:
        return None, None, error1 or error2, False

    all_packages = set(deps1.keys()) | set(deps2.keys())
    results = []
    resolved_requirements = {}
    all_resolved = True

    for pkg in sorted(all_packages):
        spec1 = deps1.get(pkg, specifiers.SpecifierSet(""))
        spec2 = deps2.get(pkg, specifiers.SpecifierSet(""))
        merged_spec = merge_specifiers(spec1, spec2)
        # Handle unconstrained cases
        if not spec1 and not spec2:
            # Both unconstrained: select latest version
            version_selected, reason = select_version(specifiers.SpecifierSet(""), pkg)
            if version_selected:
                results.append({
                    'Package': pkg,
                    'Status': '✅ Resolved',
                    'Compatible Interval': 'Any version',
                    'Selected Version': str(version_selected),
                    'Reason': reason
                })
                resolved_requirements[pkg] = str(version_selected)
            else:
                results.append({
                    'Package': pkg,
                    'Status': '⚠️ Unresolved',
                    'Compatible Interval': 'Any version',
                    'Selected Version': '-',
                    'Reason': reason
                })
                all_resolved = False
        else:        
            if merged_spec is None and (spec1 or spec2):
                results.append({
                    'Package': pkg,
                    'Status': '❌ Conflict',
                    'Compatible Interval': f"{spec1} vs {spec2}",
                    'Selected Version': '-',
                    'Reason': 'Incompatible version constraints'
                })
                all_resolved = False
            else:
                version_selected, reason = select_version(merged_spec, pkg)
                if version_selected:
                    results.append({
                        'Package': pkg,
                        'Status': '✅ Resolved',
                        'Compatible Interval': str(merged_spec),
                        'Selected Version': str(version_selected),
                        'Reason': reason
                    })
                    resolved_requirements[pkg] = str(version_selected)
                else:
                    results.append({
                        'Package': pkg,
                        'Status': '⚠️ Unresolved',
                        'Compatible Interval': str(merged_spec),
                        'Selected Version': '-',
                        'Reason': reason
                    })
                    all_resolved = False

    resolved_content = None
    download_file = None
    if all_resolved and resolved_requirements:
        resolved_content = "\n".join(f"{pkg}=={ver}" for pkg, ver in sorted(resolved_requirements.items()))
        temp_file = os.path.join(tempfile.gettempdir(), f"resolved_requirements_{uuid4().hex}.txt")
        with open(temp_file, 'w') as f:
            f.write(resolved_content)
        download_file = temp_file

    return pd.DataFrame(results), resolved_content, download_file, all_resolved

import toml

def handle_file_upload(file, textbox_content):
    """Handle file upload and return its content to display in textbox."""
    if file:
        # Determine file type based on extension
        file_name = file if isinstance(file, str) else file.name
        content = ""
        
        if file_name.endswith('.toml'):
            # Handle pyproject.toml
            if isinstance(file, str):
                with open(file, 'r', encoding='utf-8') as f:
                    toml_content = f.read()
            else:
                toml_content = file.read().decode('utf-8')
            
            # Parse TOML and extract project.dependencies
            try:
                toml_data = toml.loads(toml_content)
                dependencies = toml_data.get('project', {}).get('dependencies', [])
                if not dependencies:
                    return "No dependencies found in [project.dependencies]."
                # Format as requirements.txt style
                content = "\n".join(str(dep) for dep in dependencies)
            except toml.TomlDecodeError:
                return "Invalid TOML format in pyproject.toml."
        elif file_name.endswith('.txt'):
            # Handle requirements.txt
            if isinstance(file, str):
                with open(file, 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                content = file.read().decode('utf-8')
        else:
            return "Unsupported file type. Please upload .txt or .toml files."
        
        return content
    return textbox_content

def compare_requirements(file1_content, file2_content):
    """Compare two requirements.txt contents and return results."""
    if not file1_content or not file2_content:
        return None, None, None, False, "Please provide content for both requirements.txt files."

    results_df, resolved_content, download_file, all_resolved = analyze_requirements(file1_content, file2_content)

    if isinstance(results_df, str):
        return None, None, None, False, results_df

    return results_df, resolved_content, download_file, all_resolved, None

def create_interface():
    with gr.Blocks() as demo:
        gr.Markdown("# Dependency Resolution for 2 Python Projects")
        gr.Markdown("Enter or upload two `requirements.txt` or `pyproject.toml` files to compare and merge dependencies. The merged result appears in the third textbox if all dependencies resolve. View details in the collapsible section below.")

        with gr.Row():
            with gr.Column():
                file1_content = gr.Textbox(label="First requirements.txt", lines=10, placeholder="Enter or upload requirements.txt content...")
                file1_upload = gr.File(label="Upload First requirements.txt", file_types=[".txt",".toml"], file_count="single")
                file1_upload.change(
                    fn=handle_file_upload,
                    inputs=[file1_upload, file1_content],
                    outputs=file1_content
                )

            with gr.Column():
                file2_content = gr.Textbox(label="Second requirements.txt", lines=10, placeholder="Enter or upload requirements.txt content...")
                file2_upload = gr.File(label="Upload Second requirements.txt", file_types=[".txt",".toml"], file_count="single")
                file2_upload.change(
                    fn=handle_file_upload,
                    inputs=[file2_upload, file2_content],
                    outputs=file2_content
                )

        with gr.Row():
            compare_btn = gr.Button("Compare and Merge Dependencies")

        merged_content = gr.Textbox(label="Merged requirements.txt", lines=10, placeholder="Merged requirements.txt will appear here if all dependencies resolve.", interactive=False)
        download_btn = gr.File(label="Download Merged requirements.txt", visible=False)

        details_accordion = gr.Accordion("Merge Details", open=False)
        with details_accordion:
            results_table = gr.Dataframe(
                label="Dependency Resolution Details",
                headers=["Package", "Status", "Compatible Interval", "Selected Version", "Reason"],
                wrap=True
            )

        error_message = gr.Textbox(label="Error Message", interactive=False, visible=False)

        def update_outputs(file1_content, file2_content):
            results_df, resolved_content, download_file, all_resolved, error = compare_requirements(file1_content, file2_content)
            if error:
                return (
                    None,  # results_table
                    None,  # merged_content
                    None,  # download_btn
                    gr.update(open=True),  # details_accordion open
                    error   # error_message
                )
            return (
                results_df,
                resolved_content or "Not all dependencies could be resolved.",
                download_file,
               gr.update(open=not all_resolved),  # Open accordion if not all resolved
                None
            )

        compare_btn.click(
            fn=update_outputs,
            inputs=[file1_content, file2_content],
            outputs=[results_table, merged_content, download_btn, details_accordion, error_message]
        )

    return demo

if __name__ == "__main__":
    demo = create_interface()
    demo.launch(share=False)