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


@kopf.on.create("hematoscope.app", "v1", "jobruns")
def jobrun_create(spec, name, namespace, patch, **_):
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


@kopf.on.event("batch", "v1", "jobs")
def job_status_update(event, **_):
    job = event.get("object")
    if not job:
        return

    jobrun_name = (
        job.get("metadata", {}).get("labels", {}).get("hematoscope.app/job-run")
    )
    jobrun_namespace = job.get("metadata", {}).get("namespace")
    if not jobrun_name or not jobrun_namespace:
        return

    job_status = job.get("status", {})
    status_update = {
        "startTime": job_status.get("startTime"),
        "completionTime": job_status.get("completionTime"),
        "conditions": job_status.get("conditions", []),
        "active": job_status.get("active"),
        "succeeded": job_status.get("succeeded"),
        "failed": job_status.get("failed"),
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
