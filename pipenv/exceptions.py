import itertools
import sys
from collections import namedtuple
from traceback import format_tb

from pipenv.patched.pip._vendor.rich.console import Console
from pipenv.patched.pip._vendor.rich.text import Text
from pipenv.utils import err
from pipenv.vendor import click
from pipenv.vendor.click.exceptions import ClickException, FileError, UsageError


def unstyle(text: str) -> str:
    """Remove all styles from the given text."""
    try:
        styled_text = Text.from_markup(text)
        stripped_text = styled_text.strip_styles()
        return stripped_text.plain
    except AttributeError:
        # Fallback if the expected methods are not available
        return str(text)


KnownException = namedtuple(
    "KnownException",
    ["exception_name", "match_string", "show_from_string", "prefix"],
)
KnownException.__new__.__defaults__ = (None, None, None, "")

KNOWN_EXCEPTIONS = [
    KnownException("PermissionError", prefix="Permission Denied:"),
    KnownException(
        "VirtualenvCreationException",
        match_string="do_create_virtualenv",
        show_from_string=None,
    ),
]


def handle_exception(exc_type, exception, traceback, hook=sys.excepthook):
    from pipenv import environments

    if environments.Setting().is_verbose() or not issubclass(exc_type, ClickException):
        hook(exc_type, exception, traceback)
    else:
        tb = format_tb(traceback, limit=-6)
        lines = itertools.chain.from_iterable([frame.splitlines() for frame in tb])
        formatted_lines = []
        for line in lines:
            line = line.strip("'").strip('"').strip("\n").strip()
            if not line.startswith("File"):
                line = f"      {line}"
            else:
                line = f"  {line}"
            line = f"[{exception.__class__.__name__!s}]: {line}"
            formatted_lines.append(line)
        err.print("\n".join(formatted_lines))
        exception.show()


sys.excepthook = handle_exception


class PipenvException(ClickException):
    message = "[bold][red]ERROR[/red][/bold]: {}"

    def __init__(self, message=None, **kwargs):
        if not message:
            message = "Pipenv encountered a problem and had to exit."
        extra = kwargs.pop("extra", [])
        self.message = self.message.format(message)
        self.extra = extra

    def show(self, file=None):
        if file is None:
            file = sys.stderr
        console = Console(file=file)
        if self.extra:
            if isinstance(self.extra, str):
                self.extra = [self.extra]
            for extra in self.extra:
                console.print(extra)
        console.print(f"{self.message}")


class PipenvCmdError(PipenvException):
    def __init__(self, cmd, out="", err="", exit_code=1):
        self.cmd = cmd
        self.out = out
        self.err = err
        self.exit_code = exit_code
        message = f"Error running command: {cmd}"
        PipenvException.__init__(self, message)

    def show(self, file=None):
        console = Console(stderr=True, file=file, highlight=False)
        console.print(f"[red]Error running command:[/red] [bold]$ {self.cmd}[/bold]")
        if self.out:
            console.print(f"OUTPUT: {self.out}")
        if self.err:
            console.print(f"STDERR: {self.err}")


class JSONParseError(PipenvException):
    def __init__(self, contents="", error_text=""):
        self.error_text = error_text
        self.contents = contents
        PipenvException.__init__(self, contents)

    def show(self, file=None):
        console = Console(stderr=True, file=file, highlight=False)
        console.print(
            f"[bold][red]Failed parsing JSON results:[/red][/bold]: {self.contents}"
        )
        if self.error_text:
            console.print(f"[bold][red]ERROR TEXT:[/red][/bold]: {self.error_text}")


class PipenvUsageError(UsageError):
    def __init__(self, message=None, ctx=None, **kwargs):
        formatted_message = "{0}: {1}"
        msg_prefix = "[bold red]ERROR:[/bold red]"
        if not message:
            message = "Pipenv encountered a problem and had to exit."
        message = formatted_message.format(msg_prefix, f"[bold]{message}[/bold]")
        self.message = message
        UsageError.__init__(self, message, ctx)

    def show(self, file=None):
        hint = ""
        if self.cmd is not None and self.cmd.get_help_option(self.ctx) is not None:
            hint = f'Try "{self.ctx.command_path} {self.ctx.help_option_names[0]}" for help.\n'
        if self.ctx is not None:
            console = Console(
                stderr=True, file=file, highlight=False, force_terminal=self.ctx.color
            )
            console.print(self.ctx.get_usage() + f"\n{hint}")
        console = Console(stderr=True, file=file, highlight=False)
        console.print(self.message)


class PipenvFileError(FileError):
    formatted_message = "{} {{}} {{}}".format("[bold red]ERROR:[/bold red]")

    def __init__(self, filename, message=None, **kwargs):
        extra = kwargs.pop("extra", [])
        if not message:
            message = "[bold]Please ensure that the file exists![/bold]"
        message = self.formatted_message.format(
            f"[bold]{filename} not found![/bold]", message
        )
        FileError.__init__(self, filename=filename, hint=message, **kwargs)
        self.extra = extra

    def show(self, file=None):
        console = Console(stderr=True, file=file, highlight=False)
        if self.extra:
            if isinstance(self.extra, str):
                self.extra = [self.extra]
            for extra in self.extra:
                console.print(extra)
        console.print(self.message)


class PipfileNotFound(PipenvFileError):
    def __init__(self, filename="Pipfile", extra=None, **kwargs):
        extra = kwargs.pop("extra", [])
        message = "{} {}".format(
            "[bold red]Aborting![/bold red]",
            "[bold]Please ensure that the file exists and is located in your project root directory.[/bold]",
        )
        super().__init__(filename, message=message, extra=extra, **kwargs)


class LockfileNotFound(PipenvFileError):
    def __init__(self, filename="Pipfile.lock", extra=None, **kwargs):
        extra = kwargs.pop("extra", [])
        message = "{} {} {}".format(
            "[bold]You need to run[/bold]",
            "[bold red]$ pipenv lock[/bold red]",
            "[bold]before you can continue.[/bold]",
        )
        super().__init__(filename, message=message, extra=extra, **kwargs)


class DeployException(PipenvUsageError):
    def __init__(self, message=None, **kwargs):
        if not message:
            message = "[bold]Aborting deploy[/bold]"
        extra = kwargs.pop("extra", [])
        PipenvUsageError.__init__(self, message=message, extra=extra, **kwargs)


class PipenvOptionsError(PipenvUsageError):
    def __init__(self, option_name, message=None, ctx=None, **kwargs):
        extra = kwargs.pop("extra", [])
        PipenvUsageError.__init__(self, message=message, ctx=ctx, **kwargs)
        self.extra = extra
        self.option_name = option_name


class SystemUsageError(PipenvOptionsError):
    def __init__(self, option_name="system", message=None, ctx=None, **kwargs):
        extra = kwargs.pop("extra", [])
        extra += [
            "{}: --system is intended to be used for Pipfile installation, "
            "not installation of specific packages. Aborting.".format(
                "[bold red]Warning[/bold /red]",
            ),
        ]
        if message is None:
            message = "{} --deploy flag".format(
                "[cyan]See also: {}[/cyan]",
            )
        super().__init__(option_name, message=message, ctx=ctx, extra=extra, **kwargs)


class SetupException(PipenvException):
    def __init__(self, message=None, **kwargs):
        PipenvException.__init__(self, message, **kwargs)


class VirtualenvException(PipenvException):
    def __init__(self, message=None, **kwargs):
        if not message:
            message = (
                "There was an unexpected error while activating your virtualenv. "
                "Continuing anyway..."
            )
        PipenvException.__init__(self, message, **kwargs)


class VirtualenvActivationException(VirtualenvException):
    def __init__(self, message=None, **kwargs):
        if not message:
            message = (
                "activate_this.py not found. Your environment is most certainly "
                "not activated. Continuing anyway..."
            )
        self.message = message
        VirtualenvException.__init__(self, message, **kwargs)


class VirtualenvCreationException(VirtualenvException):
    def __init__(self, message=None, **kwargs):
        if not message:
            message = "Failed to create virtual environment."
        self.message = message
        extra = kwargs.pop("extra", None)
        if extra is not None and isinstance(extra, str):
            extra = unstyle(f"{extra}")
            if "KeyboardInterrupt" in extra:
                extra = "[red][/bold]Virtualenv creation interrupted by user[red][/bold]"
            self.extra = extra = [extra]
        VirtualenvException.__init__(self, message, extra=extra)


class UninstallError(PipenvException):
    def __init__(self, package, command, return_values, return_code, **kwargs):
        extra = [
            "{} {}".format(
                "[cyan]Attempting to run command: [/cyan]",
                f"[bold yellow]$ {command!r}[/bold yellow]",
            )
        ]
        extra.extend(
            [f"[cyan]{line.strip()}[/cyan]" for line in return_values.splitlines()]
        )
        if isinstance(package, (tuple, list, set)):
            package = " ".join(package)
        message = "{!s} {!s}...".format(
            "Failed to uninstall package(s)",
            f"[bold yellow]{package}!s[/bold yellow]",
        )
        self.exit_code = return_code
        PipenvException.__init__(self, message=message, extra=extra)
        self.extra = extra


class InstallError(PipenvException):
    def __init__(self, package, **kwargs):
        package_message = ""
        if package is not None:
            package_message = "Couldn't install package: {}\n".format(
                f"[bold]{package!s}[/bold]"
            )
        message = "{} {}".format(
            f"{package_message}",
            "[yellow]Package installation failed...[/yellow]",
        )
        extra = kwargs.pop("extra", [])
        PipenvException.__init__(self, message=message, extra=extra, **kwargs)


class DependencyConflict(PipenvException):
    def __init__(self, message):
        extra = [
            "{} {}".format(
                click.style("The operation failed...", bold=True, fg="red"),
                click.style(
                    "A dependency conflict was detected and could not be resolved.",
                    fg="red",
                ),
            )
        ]
        PipenvException.__init__(self, message, extra=extra)


class ResolutionFailure(PipenvException):
    def __init__(self, message, no_version_found=False):
        extra = (
            "Your dependencies could not be resolved. You likely have a "
            "mismatch in your sub-dependencies.\n"
            "You can use [yellow]$ pipenv run pip install <requirement_name>[/yellow] to bypass this mechanism, then run "
            "[yellow]$ pipenv graph[/yellow] to inspect the versions actually installed in the virtualenv.\n"
            "Hint: try [yellow]$ pipenv lock --pre[/yellow] if it is a pre-release dependency."
        )
        if "no version found at all" in str(message):
            message += (
                "[cyan]Please check your version specifier and version number. "
                "See PEP440 for more information.[/cyan]"
            )
        PipenvException.__init__(self, message, extra=extra)


class RequirementError(PipenvException):
    def __init__(self, req=None):
        from pipenv.utils.constants import VCS_LIST

        keys = (
            (
                "name",
                "path",
            )
            + VCS_LIST
            + ("line", "uri", "url", "relpath")
        )
        if req is not None:
            possible_display_values = [getattr(req, value, None) for value in keys]
            req_value = next(
                iter(val for val in possible_display_values if val is not None), None
            )
            if not req_value:
                getstate_fn = getattr(req, "__getstate__", None)
                slots = getattr(req, "__slots__", None)
                keys_fn = getattr(req, "keys", None)
                if getstate_fn:
                    req_value = getstate_fn()
                elif slots:
                    slot_vals = [
                        (k, getattr(req, k, None)) for k in slots if getattr(req, k, None)
                    ]
                    req_value = "\n".join([f"    {k}: {v}" for k, v in slot_vals])
                elif keys_fn:
                    values = [(k, req.get(k)) for k in keys_fn() if req.get(k)]
                    req_value = "\n".join([f"    {k}: {v}" for k, v in values])
                else:
                    req_value = getattr(req.line_instance, "line", None)
        message = click.style(
            f"Failed creating requirement instance {req_value}",
            bold=False,
            fg="reset",
            bg="reset",
        )
        extra = [str(req)]
        PipenvException.__init__(self, message, extra=extra)


def prettify_exc(error):
    """Catch known errors and prettify them instead of showing the
    entire traceback, for better UX"""
    errors = []
    for exc in KNOWN_EXCEPTIONS:
        search_string = exc.match_string if exc.match_string else exc.exception_name
        split_string = (
            exc.show_from_string if exc.show_from_string else exc.exception_name
        )
        if search_string in error:
            # for known exceptions with no display rules and no prefix
            # we should simply show nothing
            if not exc.show_from_string and not exc.prefix:
                errors.append("")
                continue
            elif exc.prefix and exc.prefix in error:
                _, error, info = error.rpartition(exc.prefix)
            else:
                _, error, info = error.rpartition(split_string)
            errors.append(f"{error} {info}")
    if not errors:
        return error

    return "\n".join(errors)
