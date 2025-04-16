import os
import kopf
import kubernetes
import yaml
from datetime import datetime, timedelta, timezone
from kubernetes.client.rest import ApiException


def get_template(namespace, name):
    crd_api = kubernetes.client.CustomObjectsApi()
    return crd_api.get_namespaced_custom_object(
        group="hematoscope.app",
        version="v1",
        namespace=namespace,
        plural="jobtemplates",
        name=name,
    )


def create_job(namespace, template, command=None, args=None):
    job_spec = template["spec"]
    job_spec = yaml.safe_load(yaml.dump(job_spec))  # Deep copy

    container = job_spec["template"]["spec"]["containers"][0]
    if command:
        container["command"] = command
    if args:
        container["args"] = args

    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "generateName": "templated-job-",
            "labels": {
                "app.kubernetes.io/name": os.getenv("APP_NAME"),
                "app.kubernetes.io/instance": os.getenv("APP_INSTANCE"),
                "app.kubernetes.io/version": os.getenv("APP_VERSION"),
                "app.kubernetes.io/managed-by": os.getenv("APP_MANAGED_BY"),
            },
        },
        "spec": job_spec,
    }
    batch_v1 = kubernetes.client.BatchV1Api()
    batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)


@kopf.on.create("hematoscope.app", "v1", "jobruns")
def jobrun_create(spec, namespace, **_):
    template_name = spec.get("templateRef")
    command = spec.get("command")
    args = spec.get("args")

    if not template_name:
        raise kopf.PermanentError("templateRef must be specified")

    template = get_template(namespace, template_name)
    create_job(namespace, template, command, args)


@kopf.on.success("batch", "v1", "jobs")
def job_success(meta, namespace, **_):
    job_name = meta["name"]
    labels = meta.get("labels", {})
    managed_by_label = os.getenv("APP_MANAGED_BY")

    if labels.get("app.kubernetes.io/managed-by") == managed_by_label:
        batch_v1 = kubernetes.client.BatchV1Api()
        batch_v1.delete_namespaced_job(
            name=job_name,
            namespace=namespace,
            body=kubernetes.client.V1DeleteOptions(propagation_policy="Foreground"),
        )


@kopf.timer(
    "hematoscope.app", "v1", interval=os.getenv("CLEANUP_INTERVAL_MINUTES") * 60
)
def cleanup_old_jobs(namespace, **_):
    managed_by_label = os.getenv("APP_MANAGED_BY")
    if not managed_by_label:
        return

    batch_v1 = kubernetes.client.BatchV1Api()
    try:
        jobs = batch_v1.list_namespaced_job(namespace=namespace)
        for job in jobs.items:
            labels = job.metadata.labels or {}
            if labels.get("app.kubernetes.io/managed-by") != managed_by_label:
                continue

            creation_timestamp = job.metadata.creation_timestamp
            if creation_timestamp and (
                datetime.now(timezone.utc) - creation_timestamp.replace(tzinfo=None)
            ) > timedelta(minutes=int(os.getenv("CLEANUP_EXIPRY_MINUTES"))):
                batch_v1.delete_namespaced_job(
                    name=job.metadata.name,
                    namespace=namespace,
                    body=kubernetes.client.V1DeleteOptions(
                        propagation_policy="Foreground"
                    ),
                )
    except ApiException as e:
        kopf.logger.error(f"Failed to clean up old jobs: {e}")
