# gateway/

A sandboxed orchestration layer for proactive (cron) loops and multi-channel notifications
(PM reminders, audit-freshness checks, overdue-invoice nudges).

Adopts OpenClaw's gateway-in-front-of-model pattern — deliberately **walled off** from privileged
capabilities, because OpenClaw's broad access has a documented attack surface.
