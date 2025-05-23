import shutil
import sys

from pipenv import exceptions
from pipenv.patched.pip._internal.build_env import get_runnable_pip
from pipenv.project import Project
from pipenv.routines.lock import do_lock
from pipenv.utils import console
from pipenv.utils.dependencies import (
    expansive_install_req_from_line,
    get_lockfile_section_using_pipfile_category,
    get_pipfile_category_using_lockfile_section,
    pep423_name,
)
from pipenv.utils.processes import run_command, subprocess_run
from pipenv.utils.requirements import BAD_PACKAGES
from pipenv.utils.resolver import venv_resolve_deps
from pipenv.utils.shell import cmd_list_to_shell, project_python
from pipenv.vendor.importlib_metadata.compat.py39 import normalized_name


def _uninstall_from_environment(project: Project, package, system=False):
    # Execute the uninstall command for the package
    with project.environment.activated() as is_active:
        if not is_active:
            return False

        console.print(f"Uninstalling {package}...", style="bold green")
        cmd = [
            project_python(project, system=system),
            get_runnable_pip(),
            "uninstall",
            package,
            "-y",
        ]
        c = run_command(cmd, is_verbose=project.s.is_verbose())
        console.print(c.stdout, style="cyan")
        if c.returncode != 0:
            console.print(f"Error occurred while uninstalling package {package}.")
            return False
    return True


def do_uninstall(
    project: Project,
    packages=None,
    editable_packages=None,
    python=False,
    system=False,
    lock=False,
    all_dev=False,
    all=False,
    pre=False,
    pypi_mirror=None,
    ctx=None,
    categories=None,
):
    # Initialization similar to the upgrade function
    if not any([packages, editable_packages, all_dev, all]):
        raise exceptions.PipenvUsageError("No package provided!", ctx=ctx)

    if not categories:
        categories = ["default"]

    lockfile_content = project.lockfile_content

    if all_dev:
        console.print(
            "Un-installing all [yellow][dev-packages][/yellow]...",
            style="bold",
        )
        # Uninstall all dev-packages from environment
        for package in project.get_pipfile_section("dev-packages"):
            _uninstall_from_environment(project, package, system=system)
        # Remove the package from the Pipfile
        if project.reset_category_in_pipfile(category="dev-packages"):
            console.print("Removed [dev-packages] from Pipfile.")
        # Finalize changes to lockfile
        lockfile_content["develop"] = {}
        lockfile_content.update({"_meta": project.get_lockfile_meta()})
        project.write_lockfile(lockfile_content)

    if all:
        console.print(
            "Un-installing all packages...",
            style="bold",
        )
        # Uninstall all packages from all groups
        for category in project.get_package_categories():
            if category in ["source", "requires"]:
                continue
            for package in project.get_pipfile_section(category):
                _uninstall_from_environment(project, package, system=system)

        # Clear all categories in the lockfile
        for category in list(lockfile_content.keys()):
            if category != "_meta":
                lockfile_content[category] = {}

        lockfile_content.update({"_meta": project.get_lockfile_meta()})
        project.write_lockfile(lockfile_content)

        # Call do_purge to remove all packages from the environment
        do_purge(project, bare=False, downloads=False, allow_global=system)
        return

    package_args = list(packages) + [f"-e {pkg}" for pkg in editable_packages]

    # Determine packages and their dependencies for removal
    for category in categories:
        category = get_lockfile_section_using_pipfile_category(
            category
        )  # In case they passed pipfile category
        pipfile_category = get_pipfile_category_using_lockfile_section(category)

        for package in package_args[:]:
            install_req, _ = expansive_install_req_from_line(package, expand_env=True)
            name, normalized_name, pipfile_entry = project.generate_package_pipfile_entry(
                install_req, package, category=pipfile_category
            )

            # Remove the package from the Pipfile
            if project.remove_package_from_pipfile(
                normalized_name, category=pipfile_category
            ):
                console.print(f"Removed {normalized_name} from Pipfile.")

            # Rebuild the dependencies for resolution from the updated Pipfile
            updated_packages = project.get_pipfile_section(pipfile_category)

            # Resolve dependencies with the package removed
            resolved_lock_data = venv_resolve_deps(
                updated_packages,
                which=project._which,
                project=project,
                lockfile={},
                pipfile_category=pipfile_category,
                pre=pre,
                allow_global=system,
                pypi_mirror=pypi_mirror,
            )

            # Determine which dependencies are no longer needed
            try:
                current_lock_data = lockfile_content[category]
                if current_lock_data:
                    deps_to_remove = [
                        dep for dep in current_lock_data if dep not in resolved_lock_data
                    ]
                    # Remove unnecessary dependencies from Pipfile and lockfile
                    for dep in deps_to_remove:
                        if (
                            category in lockfile_content
                            and dep in lockfile_content[category]
                        ):
                            del lockfile_content[category][dep]
            except KeyError:
                pass  # No lockfile data for this category

    # Finalize changes to lockfile
    lockfile_content.update({"_meta": project.get_lockfile_meta()})
    project.write_lockfile(lockfile_content)

    # Perform uninstallation of packages and dependencies
    failure = False
    for package in package_args:
        _uninstall_from_environment(project, package, system=system)

    if lock:
        do_lock(project, system=system, pypi_mirror=pypi_mirror)

    sys.exit(int(failure))


def do_purge(project, bare=False, downloads=False, allow_global=False):
    """Executes the purge functionality."""

    if downloads:
        if not bare:
            console.print("Clearing out downloads directory...", style="bold")
        shutil.rmtree(project.download_location)
        return

    # Remove comments from the output, if any.
    installed = {
        normalized_name(pkg) for pkg in project.environment.get_installed_packages()
    }
    bad_pkgs = {pep423_name(pkg) for pkg in BAD_PACKAGES}
    # Remove setuptools, pip, etc from targets for removal
    to_remove = installed - bad_pkgs

    # Skip purging if there is no packages which needs to be removed
    if not to_remove:
        if not bare:
            console.print("Found 0 installed package, skip purging.")
            console.print("Environment now purged and fresh!", style="green")
        return installed

    if not bare:
        console.print(f"Found {len(to_remove)} installed package(s), purging...")

    command = [
        project_python(project, system=allow_global),
        get_runnable_pip(),
        "uninstall",
        "-y",
    ] + list(to_remove)
    if project.s.is_verbose():
        console.print(f"$ {cmd_list_to_shell(command)}")
    c = subprocess_run(command)
    if c.returncode != 0:
        raise exceptions.UninstallError(
            installed, cmd_list_to_shell(command), c.stdout + c.stderr, c.returncode
        )
    if not bare:
        console.print(c.stdout, style="cyan")
        console.print("Environment now purged and fresh!", style="green")
    return installed
