from dataclasses import dataclass

from portal.models import Tier


@dataclass(frozen=True)
class Sample:
    slug: str
    category: str
    title: str
    tier: int
    teaser: str
    reference: str
    sections: tuple
    agreement: str = ""


COMPANY = "Alder Quay Systems"
AGREEMENT = (
    "Sample preview agreement for this fictional audit report. Use the example only "
    "to evaluate the Trust Centre. Do not distribute it as audit evidence or represent "
    "it as an independent opinion."
)

SAMPLES = (
    Sample("information-security-policy", "Policies", "Information Security Policy", Tier.BASIC,
           "How Alder Quay Systems assigns security responsibilities and manages access, assets, and exceptions.",
           "POL-01 · Revision 1.2 · Effective 1 October 2026", (
               ("Purpose and scope", "Applies to employees and contractors with access to the hosted workflow platform and its supporting corporate systems. The Head of Compliance owns this policy and reviews it annually and after significant material change."),
               ("Identity and access", "Production access requires a named account, multi-factor authentication, and approval from the system owner. Privileged access is reviewed quarterly. The service desk removes access by the end of the departing worker’s final day."),
               ("Asset and data handling", "System owners maintain an inventory and classify information as public, internal, or restricted. Restricted data is encrypted in transit and at rest. Public links to restricted data are prohibited."),
               ("Exceptions and evidence", "Exceptions must include the business reason, compensating control, accountable owner, and expiry date. Compliance records approvals and reviews open exceptions monthly."),
           )),
    Sample("incident-response-policy", "Policies", "Incident Response Policy", Tier.PRO,
           "Incident severity, escalation, evidence handling, and recovery review for the hosted platform.",
           "POL-07 · Revision 2.0 · Effective 12 October 2026", (
               ("Activation", "On-call engineering opens an incident record when an alert indicates possible compromise, loss of availability, or unauthorized disclosure. The incident commander assigns an initial severity and records the incident timeline in UTC."),
               ("Severity and escalation", "SEV-1 covers confirmed exposure of restricted data or widespread outage. The on-call lead immediately pages the incident commander, security lead, and privacy lead. SEV-2 covers contained events requiring coordinated investigation."),
               ("Containment and communication", "Responders isolate affected systems, preserve logs, and record every containment decision. The privacy lead assesses contractual and regulatory notification obligations and coordinates approved customer communications."),
               ("Recovery and follow-up", "A service owner approves return to operation after integrity checks and monitoring are in place. Within ten business days, the incident commander assigns corrective actions and records owners and due dates."),
           )),
    Sample("isms-certificate-specimen", "Certificates", "ISO/IEC 27001 Certificate Specimen", Tier.BASIC,
           "An illustrative information security management system certificate with scope and validity fields.",
           "SPECIMEN AQ-ISMS-001 · Illustrative only", (
               ("Organization", "Alder Quay Systems, a fictional provider of hosted workflow software."),
               ("Reference standard", "ISO/IEC 27001:2022. This specimen illustrates the fields commonly presented on a certificate."),
               ("Illustrative scope", "Management of information security for the design, operation, and support of the hosted workflow platform, including production operations and supporting corporate IT."),
               ("Example validity fields", "Illustrative issue date: 1 October 2026. Illustrative expiry date: 30 September 2029. The fictional identifier cannot be verified in an issuer register."),
               ("Status", "No certification body issued this specimen. It makes no claim of certification and has no accreditation mark or signature."),
           )),
    Sample("qms-certificate-specimen", "Certificates", "ISO 9001 Certificate Specimen", Tier.PRO,
           "An illustrative quality management certificate covering product delivery and customer support.",
           "SPECIMEN AQ-QMS-002 · Illustrative only", (
               ("Organization", "Alder Quay Systems, a fictional provider of hosted workflow software."),
               ("Reference standard", "ISO 9001:2015. This specimen shows an example of how certification scope and status might be presented."),
               ("Illustrative scope", "Software design, release management, onboarding, and customer support for a subscription workflow platform."),
               ("Example validity fields", "Illustrative issue date: 15 September 2026. Illustrative expiry date: 14 September 2029. The fictional identifier cannot be verified in an issuer register."),
               ("Status", "No certification body issued this specimen. It makes no claim of certification and has no accreditation mark or signature."),
           )),
    Sample("soc2-report-preview", "Reports", "SOC 2 Type II Report Preview", Tier.EXPERT,
           "Illustrative audit scope, example control tests, and a remediation item for a hosted SaaS platform.",
           "RPT-01 · Revision 1.0 · Example period April–September 2026", (
               ("Scope", "The example covers Security and Availability for the hosted workflow system. The boundary includes the production application, release pipeline, identity provider configuration, and on-call operations."),
               ("Example control test", "For 25 sampled production changes, compare approval timestamps with deployment records. In this fictional sample, 24 changes had approval before deployment. One emergency change was approved retrospectively within one business day."),
               ("Example remediation", "Engineering added a required emergency change ticket and a weekly reconciliation between deployment logs and change records. The Head of Engineering reviews the reconciliation summary monthly."),
               ("Report status", "This is a fictional excerpt that illustrates audit report structure. It contains no independent auditor opinion and cannot be relied on as assurance."),
           ), AGREEMENT),
    Sample("disaster-recovery-exercise", "Reports", "Disaster Recovery Exercise Report", Tier.PRO,
           "A sample restore exercise with recovery objectives, measured results, and follow-up actions.",
           "RPT-04 · Revision 1.1 · Exercise date 6 October 2026", (
               ("Exercise scope", "Engineering simulated loss of the primary database and restored a scheduled backup into an isolated environment. The exercise covered application connectivity, tenant permissions, and integrity checks for sampled records."),
               ("Objectives and results", "The example recovery time objective was four hours. Service validation completed after two hours and twelve minutes. The example recovery point objective was one hour. The restored data was twenty-three minutes behind the simulated failure."),
               ("Observations", "Backup integrity checks passed. The runbook pointed to an outdated dashboard. An engineer located the replacement, adding eleven minutes to the exercise."),
               ("Follow-up", "The platform owner updates the runbook and adds a quarterly dashboard-link check. The next exercise should cover a regional outage and verification of the customer notification workflow."),
           )),
)
