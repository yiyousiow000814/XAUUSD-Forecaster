"""Compatibility shim for xauusd_forecaster.ai.model_gateway."""

from xauusd_forecaster.ai.model_gateway import (
    GeminiModelGateway,
    LOCAL_TOKEN_ESTIMATOR_VERSION,
    ModelGatewayCapacityExhausted,
    ModelGatewayRequestFailed,
    ModelGatewayResponseInvalid,
    ModelRequestAccountant,
    ModelRequestUsage,
    OllamaAssistantGateway,
    T,
    post_gemini_batch_embeddings,
)

__all__ = [
    "GeminiModelGateway",
    "LOCAL_TOKEN_ESTIMATOR_VERSION",
    "ModelGatewayCapacityExhausted",
    "ModelGatewayRequestFailed",
    "ModelGatewayResponseInvalid",
    "ModelRequestAccountant",
    "ModelRequestUsage",
    "OllamaAssistantGateway",
    "T",
    "post_gemini_batch_embeddings",
]
