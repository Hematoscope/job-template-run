import logging
import os
import httpx
import kopf
import kubernetes
import yaml


logger = logging.getLogger("kopf.controller")


def get_template(namespace, name):
    crd_api = kubernetes.client.CustomObjectsApi()
    return crd_api.get_namespaced_custom_object(
        group="cellbytes.io",
        version="v1",
        namespace=namespace,
        plural="jobtemplates",
        name=name,
    )


def create_job(
    name,
    namespace,
    template,
    command=None,
    args=None,
    callback_url=None,
    callback_token=None,
):
    template = yaml.safe_load(yaml.dump(template))
    job_spec = template["spec"]

    container = job_spec["template"]["spec"]["containers"][0]
    if command:
        container["command"] = command
    if args:
        container["args"] = args

    labels = {
        "app.kubernetes.io/name": os.getenv("APP_NAME"),
        "app.kubernetes.io/instance": os.getenv("APP_INSTANCE"),
        "app.kubernetes.io/version": os.getenv("APP_VERSION"),
        "app.kubernetes.io/managed-by": os.getenv("APP_MANAGED_BY"),
        "cellbytes.io/job-template": template["metadata"]["name"],
        "cellbytes.io/job-run": name,
    }

    job_spec["template"].setdefault("metadata", {}).setdefault("labels", {}).update(
        labels
    )

    annotations = {}
    if callback_url:
        annotations["cellbytes.io/callback-url"] = callback_url
    if callback_token:
        annotations["cellbytes.io/callback-token"] = callback_token

    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "generateName": f"{template['metadata']['name']}-{name}-",
            "labels": labels,
            "annotations": annotations,
        },
        "spec": job_spec,
    }
    batch_v1 = kubernetes.client.BatchV1Api()
    batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)


@kopf.on.startup()
def configure(settings, **_):
    settings.networking.request_timeout = 60
    settings.watching.server_timeout = 60
    settings.watching.client_timeout = 60


INTERVAL = float(os.getenv("TIMER_INTERVAL"))


@kopf.timer("cellbytes.io", "v1", "jobruns", interval=INTERVAL)
def jobrun_create_timer(spec, name, namespace, patch, status, **_):
    api = kubernetes.client.BatchV1Api()
    existing_jobs = api.list_namespaced_job(
        namespace,
        label_selector=f"cellbytes.io/job-run={name}",
    )

    # Exit early if there are existing jobs or if the jobrun already succeeded or failed
    if len(existing_jobs.items) > 0 or (
        status and (status.get("succeeded") or status.get("failed"))
    ):
        return

    template_name = spec.get("templateRef")
    command = spec.get("command")
    args = spec.get("args")
    callback_url = spec.get("callbackUrl")
    callback_token = spec.get("callbackToken")

    if not template_name:
        patch.status["error"] = "templateRef must be specified"
        patch.status["failed"] = 1
        raise kopf.PermanentError("templateRef must be specified")

    try:
        template = get_template(namespace, template_name)
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:
            patch.status["error"] = f"JobTemplate '{template_name}' not found"
            patch.status["failed"] = 1
            raise kopf.PermanentError(f"JobTemplate '{template_name}' not found")
        else:
            raise
    create_job(name, namespace, template, command, args, callback_url, callback_token)


@kopf.timer("batch", "v1", "jobs", interval=INTERVAL)
def job_status_update_timer(spec, name, namespace, status, meta, **_):
    jobrun_name = meta.get("labels", {}).get("cellbytes.io/job-run")
    jobrun_namespace = meta.get("namespace")
    if not jobrun_name or not jobrun_namespace:
        return

    status_update = {
        "startTime": status.get("startTime"),
        "completionTime": status.get("completionTime"),
        "conditions": status.get("conditions", []),
        "active": status.get("active"),
        "succeeded": status.get("succeeded"),
        "failed": status.get("failed"),
    }
    crd_api = kubernetes.client.CustomObjectsApi()
    try:
        crd_api.patch_namespaced_custom_object(
            group="cellbytes.io",
            version="v1",
            namespace=jobrun_namespace,
            plural="jobruns",
            name=jobrun_name,
            body={"status": status_update},
        )
    except kubernetes.client.exceptions.ApiException as e:
        logger.warning(f"Failed to update JobRun status: {e}")

    conditions = status.get("conditions") or []
    is_failed = any(
        c.get("type") == "Failed" and c.get("status") == "True" for c in conditions
    )
    is_complete = any(
        c.get("type") == "Complete" and c.get("status") == "True" for c in conditions
    )
    callback_url = (meta.get("annotations") or {}).get("cellbytes.io/callback-url")
    callback_token = (meta.get("annotations") or {}).get("cellbytes.io/callback-token")
    callback_sent = (meta.get("annotations") or {}).get(
        "cellbytes.io/callback-sent"
    ) == "true"

    terminal_status = "Complete" if is_complete else "Failed" if is_failed else None

    if callback_url and terminal_status and not callback_sent:
        headers = {}
        if callback_token:
            headers["Authorization"] = f"Bearer {callback_token}"
        try:
            httpx.post(
                callback_url,
                json={"name": jobrun_name, "status": terminal_status},
                headers=headers,
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Failed to send callback to {callback_url}: {e}")
        # Mark sent regardless of HTTP outcome to avoid infinite retries on bad URLs
        batch_api = kubernetes.client.BatchV1Api()
        batch_api.patch_namespaced_job(
            name=name,
            namespace=namespace,
            body={"metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}},
        )
