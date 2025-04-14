import kopf
import kubernetes
import yaml


def get_template(namespace, name):
    crd_api = kubernetes.client.CustomObjectsApi()
    return crd_api.get_namespaced_custom_object(
        group="hematoscope.app",
        version="v1",
        namespace=namespace,
        plural="jobtemplates",
        name=name,
    )


def create_job(namespace, template, command):
    job_spec = template["spec"]["template"]
    job_spec = yaml.safe_load(yaml.dump(job_spec))  # Deep copy
    job_spec["spec"]["template"]["spec"]["containers"][0]["command"] = command
    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"generateName": "templated-job-"},
        "spec": job_spec["spec"],
    }
    batch_v1 = kubernetes.client.BatchV1Api()
    batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)


@kopf.on.create("hematoscope.app", "v1", "jobruns")
def jobrun_create(spec, namespace, **_):
    template_name = spec.get("templateRef")
    command = spec.get("command")

    if not command:
        raise kopf.TemporaryError("Command must be specified", delay=10)

    template = get_template(namespace, template_name)
    create_job(namespace, template, command)
