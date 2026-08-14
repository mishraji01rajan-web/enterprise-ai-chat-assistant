# Product Datasheet: CloudSync

**Document ID:** PROD-002
**Category:** Products
**Last Updated:** 2025-07-10
**Owner:** Product Marketing

## Overview
CloudSync is our managed data-synchronization product. It keeps customer
data consistent across cloud storage providers (S3, Azure Blob, GCS) with
near-real-time replication and conflict resolution.

## Editions
| Edition     | Price                | Sync frequency | Storage providers      |
|-------------|----------------------|-----------------|--------------------------|
| Basic       | $99 / month flat      | Every 15 min    | 1 provider                |
| Pro         | $299 / month flat     | Near-real-time  | up to 3 providers         |
| Enterprise  | Custom                | Real-time       | unlimited providers        |

## Key Features
- Automatic conflict resolution with configurable rules
- End-to-end encryption in transit and at rest
- Point-in-time restore (Pro and Enterprise editions)
- Compliance reporting for SOC 2 and ISO 27001 audits (Enterprise edition)

## Integration Notes
CloudSync integrates with WidgetPro (PROD-001) to trigger workflows when a
sync conflict occurs, provided both products are on Business/Pro edition or
higher.

## Deprecation Notice
CloudSync Basic edition will be sunset on 2026-12-31. Existing Basic
customers will be offered a discounted migration path to Pro edition.
