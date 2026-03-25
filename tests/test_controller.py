"""Unit tests for controller.py callback logic."""

import unittest.mock as mock

import controller


MINIMAL_TEMPLATE = {
    "metadata": {"name": "my-template"},
    "spec": {
        "template": {
            "spec": {
                "containers": [
                    {"name": "busybox", "image": "busybox", "command": ["echo"]}
                ],
                "restartPolicy": "Never",
            }
        }
    },
}


# ---------------------------------------------------------------------------
# create_job: callback URL/token annotations
# ---------------------------------------------------------------------------


def test_create_job_sets_callback_url_annotation():
    with mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch:
        mock_create = mock_batch.return_value.create_namespaced_job
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
            callback_url="https://example.com/cb",
        )
        mock_create.assert_called_once()
        body = mock_create.call_args.kwargs["body"]
        assert (
            body["metadata"]["annotations"]["cellbytes.io/callback-url"]
            == "https://example.com/cb"
        )
        assert "cellbytes.io/callback-token" not in body["metadata"]["annotations"]


def test_create_job_sets_callback_token_annotation():
    with mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch:
        mock_create = mock_batch.return_value.create_namespaced_job
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
            callback_url="https://example.com/cb",
            callback_token="tok123",
        )
        mock_create.assert_called_once()
        body = mock_create.call_args.kwargs["body"]
        assert (
            body["metadata"]["annotations"]["cellbytes.io/callback-url"]
            == "https://example.com/cb"
        )
        assert (
            body["metadata"]["annotations"]["cellbytes.io/callback-token"] == "tok123"
        )


def test_create_job_no_callback_url_no_annotations():
    with mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch:
        mock_create = mock_batch.return_value.create_namespaced_job
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
        )
        mock_create.assert_called_once()
        body = mock_create.call_args.kwargs["body"]
        assert body["metadata"]["annotations"] == {}


# ---------------------------------------------------------------------------
# Helpers for job_status_update_timer tests
# ---------------------------------------------------------------------------


def _make_complete_conditions():
    return [{"type": "Complete", "status": "True"}]


def _make_failed_conditions():
    return [{"type": "Failed", "status": "True"}]


def _call_timer(
    *,
    conditions,
    callback_url=None,
    callback_token=None,
    callback_sent=None,
    jobrun_name="my-run",
    name="my-job",
    namespace="default",
):
    annotations = {}
    if callback_url is not None:
        annotations["cellbytes.io/callback-url"] = callback_url
    if callback_token is not None:
        annotations["cellbytes.io/callback-token"] = callback_token
    if callback_sent is not None:
        annotations["cellbytes.io/callback-sent"] = callback_sent

    meta = {
        "labels": {"cellbytes.io/job-run": jobrun_name},
        "namespace": namespace,
        "annotations": annotations if annotations else None,
    }
    status = {"conditions": conditions}

    controller.job_status_update_timer(
        spec={}, name=name, namespace=namespace, status=status, meta=meta
    )  # pyright: ignore[reportCallIssue]


# ---------------------------------------------------------------------------
# job_status_update_timer: callback firing
# ---------------------------------------------------------------------------


def test_callback_fired_on_complete_with_token():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch,
        mock.patch("controller.httpx.post") as mock_post,
    ):
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_timer(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            callback_token="bearer-tok",
        )

        mock_post.assert_called_once_with(
            "https://example.com/cb",
            json={"name": "my-run", "status": "Complete"},
            headers={"Authorization": "Bearer bearer-tok"},
            timeout=10,
        )
        mock_patch_job.assert_called_once_with(
            name="my-job",
            namespace="default",
            body={"metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}},
        )


def test_callback_fired_on_failed_without_token():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch,
        mock.patch("controller.httpx.post") as mock_post,
    ):
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_timer(
            conditions=_make_failed_conditions(),
            callback_url="https://example.com/cb",
        )

        mock_post.assert_called_once_with(
            "https://example.com/cb",
            json={"name": "my-run", "status": "Failed"},
            headers={},
            timeout=10,
        )
        mock_patch_job.assert_called_once()


def test_callback_not_fired_when_already_sent():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _call_timer(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            callback_sent="true",
        )

        mock_post.assert_not_called()


def test_callback_not_fired_when_not_terminal():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _call_timer(
            conditions=[{"type": "Active", "status": "True"}],
            callback_url="https://example.com/cb",
        )

        mock_post.assert_not_called()


def test_callback_not_fired_when_no_url():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _call_timer(conditions=_make_complete_conditions())

        mock_post.assert_not_called()


def test_annotation_marked_sent_even_when_http_call_fails():
    with (
        mock.patch("controller.kubernetes.client.CustomObjectsApi"),
        mock.patch("controller.kubernetes.client.BatchV1Api") as mock_batch,
        mock.patch(
            "controller.httpx.post", side_effect=Exception("connection refused")
        ) as mock_post,
    ):
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_timer(
            conditions=_make_complete_conditions(),
            callback_url="https://bad-url.example.com/cb",
        )

        mock_post.assert_called_once()
        # Annotation must still be patched despite the HTTP failure
        mock_patch_job.assert_called_once_with(
            name="my-job",
            namespace="default",
            body={"metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}},
        )
