# AWS Pricing API v2 Research

**Research Date**: February 5, 2026  
**Task**: 5.1 - Research AWS Pricing API  
**Status**: ✅ Complete

---

## 📚 Overview

AWS provides pricing information through the **AWS Price List Query API** (Pricing API). This API allows programmatic access to AWS service pricing data via boto3.

---

## 🔗 API Endpoints

- **Primary**: `api.pricing.us-east-1.amazonaws.com` (us-east-1)
- **Secondary**: `api.pricing.ap-south-1.amazonaws.com` (ap-south-1)
- **EC2 Service Code**: `AmazonEC2`

---

## 🔐 Authentication

- **Methods**: IAM roles, access keys, AWS profiles, temporary credentials (STS)
- **boto3**: Uses standard credential chain (env vars → `~/.aws/credentials` → IAM role)
- **IAM**: Needs `pricing:DescribeServices`, `pricing:GetAttributeValues`, `pricing:GetProducts`, etc.

---

## 📊 API Operations

| Operation | Purpose |
|-----------|---------|
| **DescribeServices** | List services or get EC2 attribute names |
| **GetAttributeValues** | Get values for an attribute (e.g. instanceType, location) |
| **GetProducts** | Get products/prices matching filters (main call for pricing) |
| **ListPriceLists** / **GetPriceListFileUrl** | Bulk price list files |

**GetProducts filters**: TERM_MATCH, EQUALS, CONTAINS, ANY_OF, NONE_OF. Max 50 filters, pagination via NextToken, MaxResults (e.g. 100).

---

## 🚦 Rate Limits (from AWS Billing docs)

**Price List Query API** – token bucket, per account per region:

| Operation | Token bucket size | Refill rate/sec |
|-----------|-------------------|------------------|
| DescribeServices | 10 | 5 |
| GetAttributeValues | 10 | 5 |
| GetProducts | 10 | 5 |

**Implication**: ~5 requests/second per operation; use exponential backoff on 429.

---

## 🔍 EC2 Attributes (examples)

- **instanceType**, **location**, **operatingSystem**, **tenancy**, **term** (OnDemand/Reserved/Spot)
- **instanceFamily**, **vcpu**, **memory** (e.g. "4 GiB"), **storage**, **networkPerformance**

**Spot**: Spot prices are **not** in the Pricing API; use EC2 **Spot Price API** (`describe_spot_price_history`) separately.

---

## 💡 Implementation Notes

1. **Currency**: All prices USD → use existing FX conversion.
2. **Location**: Uses friendly names (e.g. "EU (Ireland)") → map to region codes (eu-west-1).
3. **Pagination**: Use NextToken; recommend MaxResults=100.
4. **Errors**: Handle 429 with exponential backoff.

---

## 📚 References

- [Using Price List Query API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html)
- [Billing limits (Price List API quotas)](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/billing-limits.html)
- [boto3 Pricing client](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/pricing.html)

---

**Status**: ✅ Task 5.1 complete. Ready for Task 5.2 (AWS connector skeleton).
