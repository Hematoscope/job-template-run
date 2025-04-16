import os
import kopf
import kubernetes
import yaml

from collections import defaultdict


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


@kopf.on.create("hematoscope.app", "v1", "jobruns")
def jobrun_create(spec, name, namespace, **_):
    template_name = spec.get("templateRef")
    command = spec.get("command")
    args = spec.get("args")

    if not template_name:
        raise kopf.PermanentError("templateRef must be specified")

    template = get_template(namespace, template_name)
    create_job(name, namespace, template, command, args)
