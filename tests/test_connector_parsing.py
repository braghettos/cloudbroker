"""Unit tests for connector parsing functions (no DB or network)."""

import pytest


class TestAwsParsing:
    """Tests for connectors.aws parsing functions."""

    def test_parse_memory_gb_standard(self):
        from connectors.aws import _parse_memory_gb

        assert _parse_memory_gb("4 GiB") == 4.0
        assert _parse_memory_gb("16 GiB") == 16.0
        assert _parse_memory_gb("0.5 GiB") == 0.5

    def test_parse_memory_gb_edge_cases(self):
        from connectors.aws import _parse_memory_gb

        assert _parse_memory_gb("") is None
        assert _parse_memory_gb("invalid") is None
        assert _parse_memory_gb("4 GB") is None  # AWS uses GiB

    def test_parse_vcpu(self):
        from connectors.aws import _parse_vcpu

        assert _parse_vcpu("2") == 2
        assert _parse_vcpu("16") == 16
        assert _parse_vcpu("") == 0
        assert _parse_vcpu("invalid") == 0
        # Fractional vCPUs round up to 1 (t2.nano = 0.5)
        assert _parse_vcpu("0.5") == 1

    def test_map_physical_processor_intel(self):
        from connectors.aws import _map_aws_physical_processor_to_arch

        assert _map_aws_physical_processor_to_arch("Intel Xeon Platinum 8175") == "x86_64"
        assert _map_aws_physical_processor_to_arch("Intel Xeon E5-2686 v4") == "x86_64"

    def test_map_physical_processor_amd(self):
        from connectors.aws import _map_aws_physical_processor_to_arch

        assert _map_aws_physical_processor_to_arch("AMD EPYC 7R32") == "x86_64"
        assert _map_aws_physical_processor_to_arch("AMD EPYC 7R13") == "x86_64"

    def test_map_physical_processor_graviton(self):
        from connectors.aws import _map_aws_physical_processor_to_arch

        assert _map_aws_physical_processor_to_arch("AWS Graviton4 Processor") == "arm64"
        assert _map_aws_physical_processor_to_arch("AWS Graviton2 Processor") == "arm64"

    def test_map_physical_processor_none(self):
        from connectors.aws import _map_aws_physical_processor_to_arch

        assert _map_aws_physical_processor_to_arch(None) is None
        assert _map_aws_physical_processor_to_arch("") is None
        assert _map_aws_physical_processor_to_arch("   ") is None

    def test_map_physical_processor_unknown_defaults_x86(self):
        from connectors.aws import _map_aws_physical_processor_to_arch

        assert _map_aws_physical_processor_to_arch("Some Unknown Processor") == "x86_64"

    def test_extract_ondemand_price_usd(self):
        from connectors.aws import _extract_ondemand_price_usd

        product = {
            "terms": {
                "OnDemand": {
                    "SKUABC.123": {
                        "priceDimensions": {
                            "SKUABC.123.456": {
                                "unit": "Hrs",
                                "pricePerUnit": {"USD": "0.0464"},
                            }
                        }
                    }
                }
            }
        }
        assert _extract_ondemand_price_usd(product) == pytest.approx(0.0464)

    def test_extract_ondemand_price_usd_no_terms(self):
        from connectors.aws import _extract_ondemand_price_usd

        assert _extract_ondemand_price_usd({}) is None
        assert _extract_ondemand_price_usd({"terms": {}}) is None
        assert _extract_ondemand_price_usd({"terms": {"OnDemand": None}}) is None


class TestAzureParsing:
    """Tests for connectors.azure parsing functions."""

    def test_parse_vm_specs_d_series(self):
        from connectors.azure import parse_azure_vm_specs_heuristic

        vcpu, ram = parse_azure_vm_specs_heuristic("Standard_D4s_v3")
        assert vcpu == 4
        assert ram == 16.0  # D-series: 4 GB per vCPU

    def test_parse_vm_specs_b_series(self):
        from connectors.azure import parse_azure_vm_specs_heuristic

        vcpu, ram = parse_azure_vm_specs_heuristic("Standard_B2s")
        assert vcpu == 2
        assert ram == 4.0  # B-series: 2 GB per vCPU

    def test_parse_vm_specs_empty(self):
        from connectors.azure import parse_azure_vm_specs_heuristic

        vcpu, ram = parse_azure_vm_specs_heuristic("")
        assert vcpu == 1
        assert ram == 1.0

    def test_parse_vm_specs_no_match(self):
        from connectors.azure import parse_azure_vm_specs_heuristic

        vcpu, ram = parse_azure_vm_specs_heuristic("NoNumberHere")
        assert vcpu == 1
        assert ram == 1.0

    def test_extract_hourly_price_usd(self):
        from connectors.azure import _extract_hourly_price_usd

        # Azure Retail Prices API uses "retailPrice"
        item = {"retailPrice": 0.192, "unitOfMeasure": "1 Hour"}
        assert _extract_hourly_price_usd(item) == pytest.approx(0.192)
        # Also accepts legacy "unitPrice" field
        item2 = {"unitPrice": 0.192, "unitOfMeasure": "1 Hour"}
        assert _extract_hourly_price_usd(item2) == pytest.approx(0.192)

    def test_extract_hourly_price_usd_zero(self):
        from connectors.azure import _extract_hourly_price_usd

        assert _extract_hourly_price_usd({"retailPrice": 0.0, "unitOfMeasure": "1 Hour"}) is None
        assert _extract_hourly_price_usd({"unitPrice": 0.0, "unitOfMeasure": "1 Hour"}) is None


class TestUpcloudParsing:
    """Tests for connectors.upcloud parsing functions."""

    def test_zone_info_finnish(self):
        from connectors.upcloud import _zone_info

        info = _zone_info("fi-hel1", "Helsinki #1")
        assert info["country"] == "FI"
        assert info["is_eu"] is True
        assert info["name"] == "Helsinki #1"

    def test_zone_info_german(self):
        from connectors.upcloud import _zone_info

        info = _zone_info("de-fra1", "Frankfurt #1")
        assert info["country"] == "DE"
        assert info["is_eu"] is True

    def test_zone_info_us(self):
        from connectors.upcloud import _zone_info

        info = _zone_info("us-chi1", "Chicago #1")
        assert info["country"] == "US"
        assert info["is_eu"] is False

    def test_zone_info_unknown(self):
        from connectors.upcloud import _zone_info

        info = _zone_info("xx-zone1", "")
        assert info["country"] == "XX"
        assert info["is_eu"] is False


class TestScalewayParsing:
    """Tests for connectors.scaleway parsing functions."""

    def test_infer_family_general_purpose(self):
        from connectors.scaleway import _infer_family

        assert _infer_family("GP1-S") == "general-purpose"
        assert _infer_family("gp1-xs") == "general-purpose"

    def test_infer_family_development(self):
        from connectors.scaleway import _infer_family

        assert _infer_family("DEV1-S") == "general-purpose"
        assert _infer_family("STARDUST1-S") == "general-purpose"

    def test_infer_family_performance(self):
        from connectors.scaleway import _infer_family

        assert _infer_family("PRO2-S") == "compute-optimized"

    def test_infer_family_gpu(self):
        from connectors.scaleway import _infer_family

        assert _infer_family("GPU-3070-S") == "gpu"
        assert _infer_family("H100-1-80G") == "gpu"
        assert _infer_family("L40S-1-48G") == "gpu"

    def test_infer_family_default(self):
        from connectors.scaleway import _infer_family

        assert _infer_family("UNKNOWN-1") == "general-purpose"


class TestApiUtils:
    """Tests for shared API utility functions."""

    def test_parse_id_or_slug_none(self):
        from app.api.utils import parse_id_or_slug

        assert parse_id_or_slug(None) is None

    def test_parse_id_or_slug_numeric(self):
        from app.api.utils import parse_id_or_slug

        result = parse_id_or_slug("42")
        assert result == 42
        assert isinstance(result, int)

    def test_parse_id_or_slug_string(self):
        from app.api.utils import parse_id_or_slug

        result = parse_id_or_slug("hetzner")
        assert result == "hetzner"
        assert isinstance(result, str)
