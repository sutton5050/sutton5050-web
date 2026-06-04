import aws_cdk as cdk
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3deploy,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_certificatemanager as acm,
)
from constructs import Construct


class FrontendStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ── S3 bucket ───────────────────────────────────────────────
        bucket = s3.Bucket(
            self, "Bucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── ACM cert (us-east-1 for CloudFront) ────────────────────
        cert = acm.Certificate.from_certificate_arn(
            self, "Certificate",
            "arn:aws:acm:us-east-1:902672427642:certificate/60d70fbe-d2d5-4417-9e90-aafc4958cc56",
        )

        # ── CloudFront Function — sub-path routing ──────────────────
        # Handles clean URLs for each SPA-style sub-path:
        #   /passwords              → 301 redirect to /passwords/
        #   /passwords/<path>       → rewrite to /passwords/index.html
        #   /millywhitefield        → 301 redirect to /millywhitefield/
        #   /millywhitefield/<path> → rewrite to /millywhitefield/index.html
        router = cloudfront.Function(
            self, "PasswordsRouter",
            code=cloudfront.FunctionCode.from_inline("""
function handler(event) {
    var uri = event.request.uri;

    // /passwords routing
    if (uri === '/passwords') {
        return {
            statusCode: 301,
            statusDescription: 'Moved Permanently',
            headers: { location: { value: '/passwords/' } }
        };
    }
    if (uri.startsWith('/passwords/') && uri.lastIndexOf('.') < uri.lastIndexOf('/')) {
        event.request.uri = '/passwords/index.html';
        return event.request;
    }

    // /millywhitefield routing
    if (uri === '/millywhitefield') {
        return {
            statusCode: 301,
            statusDescription: 'Moved Permanently',
            headers: { location: { value: '/millywhitefield/' } }
        };
    }
    if (uri.startsWith('/millywhitefield/') && uri.lastIndexOf('.') < uri.lastIndexOf('/')) {
        event.request.uri = '/millywhitefield/index.html';
        return event.request;
    }

    return event.request;
}
"""),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )

        # ── CloudFront ──────────────────────────────────────────────
        distribution = cloudfront.Distribution(
            self, "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=router,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            domain_names=["sutton5050.com", "www.sutton5050.com"],
            certificate=cert,
            default_root_object="index.html",  # serves frontend-root/index.html at /
        )

        # ── Deploy landing page ─────────────────────────────────────
        # index.html at S3 root → served at sutton5050.com/
        # prune=False so this deployment never deletes files outside frontend-root/
        # (e.g. the passwords/ prefix managed by DeployPasswords below)
        s3deploy.BucketDeployment(
            self, "DeployRoot",
            sources=[s3deploy.Source.asset("../dashboard")],
            destination_bucket=bucket,
            prune=False,
            distribution=distribution,
            distribution_paths=["/index.html", "/styles.css", "/cards.js", "/vault.js"],
        )

        # ── Deploy Milly Whitefield portfolio ───────────────────────
        # Served at sutton5050.com/millywhitefield/
        # Self-contained in milly-whitefield/ — easy to move to its own
        # domain later by copying the folder and removing this block.
        s3deploy.BucketDeployment(
            self, "DeployMillyWhitefield",
            sources=[s3deploy.Source.asset("../milly-whitefield")],
            destination_bucket=bucket,
            destination_key_prefix="millywhitefield",
            prune=False,
            distribution=distribution,
            distribution_paths=["/millywhitefield/*"],
        )

        # ── Outputs ─────────────────────────────────────────────────
        # BucketName and DistributionId are read by the CI deploy workflow
        # to run aws s3 sync for the password manager files directly —
        # more reliable than CDK BucketDeployment which skips re-runs
        # when the source hash hasn't changed.
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
        cdk.CfnOutput(self, "DistributionId", value=distribution.distribution_id)

        # ── Route53 ─────────────────────────────────────────────────
        zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "Zone",
            hosted_zone_id="Z03151509FL1UXA4XHI7",
            zone_name="sutton5050.com",
        )

        route53.ARecord(
            self, "ARecord",
            zone=zone,
            record_name="sutton5050.com",
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )

        route53.ARecord(
            self, "WwwARecord",
            zone=zone,
            record_name="www.sutton5050.com",
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
