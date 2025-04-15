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
        "metadata": {"generateName": "templated-job-"},
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
