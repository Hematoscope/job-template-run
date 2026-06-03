import logging
import os
import httpx
import kopf
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


logger = logging.getLogger("kopf.controller")


@kopf.on.login()
def login(**kwargs):
    # kopf's default client-piggybacking login reads the API token from the
    # kubernetes client's api_key['authorization'], but kubernetes>=33 stores it
    # under api_key['BearerToken']. That mismatch makes kopf's watch/patch stream
    # fall back to anonymous, 403-rejected requests, so nothing reconciles. Read
    # the in-cluster service-account token directly to stay independent of the
    # client's key naming, falling back to a kubeconfig for local runs.
    return kopf.login_with_service_account(**kwargs) or kopf.login_with_kubeconfig(
        **kwargs
    )


def get_template(namespace, name):
    crd_api = client.CustomObjectsApi()
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
    batch_v1 = client.BatchV1Api()
    # The client serializes a plain dict body; the stub only types V1Job.
    batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)  # pyright: ignore[reportArgumentType]


@kopf.on.startup()
def configure(settings, **_):
    # Load credentials for our own kubernetes client calls (the @kopf.on.login
    # handler only authenticates kopf's own watch/patch stream).
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    # kopf >=1.44 detects silently dropped watch connections behind cloud load
    # balancers (where the operator<->LB socket stays open while the LB<->API
    # connection dies) by requesting bookmark heartbeats and reconnecting when
    # no event arrives within inactivity_timeout.
    settings.networking.request_timeout = 60
    settings.watching.inactivity_timeout = 70
    settings.watching.server_timeout = 600


INTERVAL = float(os.getenv("TIMER_INTERVAL", "30"))


@kopf.timer("cellbytes.io", "v1", "jobruns", interval=INTERVAL)
def jobrun_create_timer(spec, name, namespace, patch, status, **_):
    api = client.BatchV1Api()
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
    except ApiException as e:
        if e.status == 404:
            patch.status["error"] = f"JobTemplate '{template_name}' not found"
            patch.status["failed"] = 1
            raise kopf.PermanentError(f"JobTemplate '{template_name}' not found")
        else:
            raise
    create_job(name, namespace, template, command, args, callback_url, callback_token)


@kopf.timer(
    "batch",
    "v1",
    "jobs",
    interval=INTERVAL,
    labels={"cellbytes.io/job-run": kopf.PRESENT},
)
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
    crd_api = client.CustomObjectsApi()
    try:
        crd_api.patch_namespaced_custom_object(
            group="cellbytes.io",
            version="v1",
            namespace=jobrun_namespace,
            plural="jobruns",
            name=jobrun_name,
            body={"status": status_update},
        )
    except ApiException as e:
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
        delivered = False
        try:
            response = httpx.post(
                callback_url,
                json={"name": jobrun_name, "status": terminal_status},
                headers=headers,
                timeout=10,
            )
            # 2xx means accepted; 4xx means our request is malformed/unauthorized
            # and retrying will not help, so both count as delivered. A 5xx is a
            # transient server-side error worth retrying.
            delivered = response.status_code < 500
            if not delivered:
                logger.warning(
                    f"Callback to {callback_url} returned {response.status_code}, "
                    "will retry on next tick"
                )
        except Exception as e:
            # Network-level failure (app rolling out, DNS blip, timeout). Leave the
            # callback unsent so the next timer tick retries it.
            logger.warning(f"Failed to send callback to {callback_url}: {e}")

        # Only mark sent once the callback was actually delivered. Retries are
        # naturally bounded by the job's ttlSecondsAfterFinished: once the job is
        # garbage-collected the timer stops firing for it.
        if delivered:
            batch_api = client.BatchV1Api()
            batch_api.patch_namespaced_job(
                name=name,
                namespace=namespace,
                body={
                    "metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}
                },
            )
