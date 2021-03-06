# System documented in https://zulip.readthedocs.io/en/latest/subsystems/logging.html
from collections import defaultdict
from typing import Any, Dict

from django.conf import settings
from django.core.mail import mail_admins
from django.http import HttpResponse
from django.utils.translation import ugettext as _

from zerver.filters import clean_data_from_query_parameters
from zerver.lib.actions import internal_send_stream_message
from zerver.lib.response import json_error, json_success
from zerver.models import get_stream, get_system_bot


def format_email_subject(email_subject: str) -> str:
    """
    Escape CR and LF characters.
    """
    return email_subject.replace('\n', '\\n').replace('\r', '\\r')

def logger_repr(report: Dict[str, Any]) -> str:
    return "Logger {logger_name}, from module {log_module} line {log_lineno}:".format(**report)

def user_info_str(report: Dict[str, Any]) -> str:
    if report['user_full_name'] and report['user_email']:
        user_info = "{user_full_name} ({user_email})".format(**report)
    else:
        user_info = "Anonymous user (not logged in)"

    user_info += " on {deployment} deployment".format(**report)
    return user_info

def deployment_repr(report: Dict[str, Any]) -> str:
    deployment = 'Deployed code:\n'
    for field, val in report['deployment_data'].items():
        deployment += f'- {field}: {val}\n'
    return deployment

def notify_browser_error(report: Dict[str, Any]) -> None:
    report = defaultdict(lambda: None, report)
    if settings.ERROR_BOT:
        zulip_browser_error(report)
    email_browser_error(report)

def email_browser_error(report: Dict[str, Any]) -> None:
    email_subject = f"Browser error for {user_info_str(report)}"

    body = """\
User: {user_full_name} <{user_email}> on {deployment}

Message:
{message}

Stacktrace:
{stacktrace}

IP address: {ip_address}
User agent: {user_agent}
href: {href}
Server path: {server_path}
Deployed version: {version}
""".format(**report)

    more_info = report['more_info']
    if more_info is not None:
        body += "\nAdditional information:"
        for (key, value) in more_info.items():
            body += f"\n  {key}: {value}"

    body += "\n\nLog:\n{log}".format(**report)

    mail_admins(email_subject, body)

def zulip_browser_error(report: Dict[str, Any]) -> None:
    email_subject = "JS error: {user_email}".format(**report)

    user_info = user_info_str(report)

    body = f"User: {user_info}\n"
    body += "Message: {message}\n".format(**report)

    error_bot = get_system_bot(settings.ERROR_BOT)
    realm = error_bot.realm
    errors_stream = get_stream('errors', realm)

    internal_send_stream_message(
        realm,
        error_bot,
        errors_stream,
        format_email_subject(email_subject),
        body,
    )

def notify_server_error(report: Dict[str, Any], skip_error_zulip: bool=False) -> None:
    report = defaultdict(lambda: None, report)
    email_server_error(report)
    if settings.ERROR_BOT and not skip_error_zulip:
        zulip_server_error(report)

def zulip_server_error(report: Dict[str, Any]) -> None:
    email_subject = '{node}: {message}'.format(**report)

    logger_str = logger_repr(report)
    user_info = user_info_str(report)
    deployment = deployment_repr(report)

    if report['has_request']:
        request_repr = """\
Request info:
~~~~
- path: {path}
- {method}: {data}
""".format(**report)
        for field in ["REMOTE_ADDR", "QUERY_STRING", "SERVER_NAME"]:
            val = report.get(field.lower())
            if field == "QUERY_STRING":
                val = clean_data_from_query_parameters(str(val))
            request_repr += f"- {field}: \"{val}\"\n"
        request_repr += "~~~~"
    else:
        request_repr = "Request info: none"

    message = f"""{logger_str}
Error generated by {user_info}

~~~~ pytb
{report['stack_trace']}

~~~~
{deployment}
{request_repr}"""

    error_bot = get_system_bot(settings.ERROR_BOT)
    realm = error_bot.realm
    errors_stream = get_stream('errors', realm)

    internal_send_stream_message(
        realm,
        error_bot,
        errors_stream,
        format_email_subject(email_subject),
        message,
    )

def email_server_error(report: Dict[str, Any]) -> None:
    email_subject = '{node}: {message}'.format(**report)

    logger_str = logger_repr(report)
    user_info = user_info_str(report)
    deployment = deployment_repr(report)

    if report['has_request']:
        request_repr = """\
Request info:
- path: {path}
- {method}: {data}
""".format(**report)
        for field in ["REMOTE_ADDR", "QUERY_STRING", "SERVER_NAME"]:
            val = report.get(field.lower())
            if field == "QUERY_STRING":
                val = clean_data_from_query_parameters(str(val))
            request_repr += f"- {field}: \"{val}\"\n"
    else:
        request_repr = "Request info: none\n"

    message = f"""\
{logger_str}
Error generated by {user_info}

{report['stack_trace']}

{deployment}

{request_repr}"""

    mail_admins(format_email_subject(email_subject), message, fail_silently=True)

def do_report_error(deployment_name: str, type: str, report: Dict[str, Any]) -> HttpResponse:
    report['deployment'] = deployment_name
    if type == 'browser':
        notify_browser_error(report)
    elif type == 'server':
        notify_server_error(report)
    else:
        return json_error(_("Invalid type parameter"))
    return json_success()
