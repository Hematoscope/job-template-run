import logging
import os
import kopf
import kubernetes
import yaml


logger = logging.getLogger("kopf.controller")


def get_template(namespace, name):
    crd_api = kubernetes.client.CustomObjectsApi()
    return crd_api.get_namespaced_custom_object(
        group="hematoscope.app",
        version="v1",
        namespace=namespace,
        plural="jobtemplates",
        name=name,
    )


def create_job(name, namespace, template, command=None, args=None):
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
        "hematoscope.app/job-template": template["metadata"]["name"],
        "hematoscope.app/job-run": name,
    }

    job_spec["template"].setdefault("metadata", {}).setdefault("labels", {}).update(
        labels
    )

    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "generateName": f"{template['metadata']['name']}-{name}-",
            "labels": labels,
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


@kopf.timer("hematoscope.app", "v1", "jobruns", interval=INTERVAL)
def jobrun_create_timer(spec, name, namespace, patch, status, **_):
    api = kubernetes.client.BatchV1Api()
    existing_jobs = api.list_namespaced_job(
        namespace,
        label_selector=f"hematoscope.app/job-run={name}",
    )

    # Exit early if there are existing jobs or if the jobrun already succeeded or failed
    if len(existing_jobs.items) > 0 or (
        status and (status.get("succeeded") or status.get("failed"))
    ):
        return

    template_name = spec.get("templateRef")
    command = spec.get("command")
    args = spec.get("args")

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
    create_job(name, namespace, template, command, args)


@kopf.timer("batch", "v1", "jobs", interval=INTERVAL)
def job_status_update_timer(spec, name, namespace, status, meta, **_):
    jobrun_name = meta.get("labels", {}).get("hematoscope.app/job-run")
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
            group="hematoscope.app",
            version="v1",
            namespace=jobrun_namespace,
            plural="jobruns",
            name=jobrun_name,
            body={"status": status_update},
        )
    except kubernetes.client.exceptions.ApiException as e:
        logger.warning(f"Failed to update JobRun status: {e}")
