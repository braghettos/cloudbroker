# AWS Reserved Instance Pricing Research

**Research Date**: February 2026  
**Task**: 6.2 - AWS reserved instance pricing (research)  
**Status**: ✅ Complete

---

## Overview

Reserved Instance (RI) pricing is available through the **same AWS Price List Query API** (GetProducts) used for on-demand. Use filters to request `termType=Reserved` and optionally narrow by lease term, offering class, and payment option.

---

## API

- **Same client**: `boto3.client("pricing", region_name="us-east-1")`
- **Same operation**: `GetProducts` with `ServiceCode=AmazonEC2`
- **Filter**: Add `{"Type": "TERM_MATCH", "Field": "termType", "Value": "Reserved"}` (plus location, operatingSystem, tenancy, productFamily as for on-demand)

---

## RI-specific attributes (in product.attributes / terms)

- **LeaseContractLength**: `1yr` or `3yr`
- **OfferingClass**: `standard` or `convertible`
- **PurchaseOption**: `No Upfront`, `Partial Upfront`, `All Upfront`

In the **terms** object, RI products use `terms.Reserved` (not `terms.OnDemand`). Each reserved term has:

- **Upfront fee** (if any): price dimension with description containing "Upfront" and `pricePerUnit.USD`
- **Recurring hourly**: price dimension with `unit: "Hrs"` and `pricePerUnit.USD`

Effective hourly rate for comparison with on-demand = (upfront amortized over term hours) + hourly rate.

---

## Schema implications

- **Current model**: `Price.purchase_model` already has `reserved`; same (instance_type_id, region_id, purchase_model) key.
- **Options**:
  1. **Single reserved price row**: Store one effective USD/hour per (instance_type, region) per RI “flavor” (e.g. 1yr no upfront), deriving from upfront + hourly in the API response.
  2. **Richer model**: Add optional columns (e.g. `lease_term`, `payment_option`) or store RI details in JSON; then recommendations can compare on-demand vs 1yr no upfront vs 3yr all upfront, etc.

Recommendation: Start with (1)—store effective hourly rate for one or two common RI variants (e.g. 1yr no upfront, 3yr all upfront) with `purchase_model=reserved`; extend schema later if needed.

---

## Rate limits and pagination

Same as on-demand: token bucket ~5 req/s for GetProducts; use NextToken and MaxResults=100; respect 0.25s delay between pages.

---

## References

- [AWS Price List Query API – GetProducts](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html)
- [EC2 Reserved Instances](https://aws.amazon.com/ec2/pricing/reserved-instances/)
- [DescribeReservedInstancesOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeReservedInstancesOfferings.html) (EC2 API; different from Pricing API)

---

**Status**: ✅ Task 6.2 complete. Implementation can add RI ingestion using GetProducts with termType=Reserved and parsing terms.Reserved.
