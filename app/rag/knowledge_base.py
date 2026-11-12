from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    doc_id: str
    title: str
    content: str


MOCK_KNOWLEDGE_BASE: tuple[KnowledgeDocument, ...] = (
    KnowledgeDocument(
        doc_id='product_001',
        title='云朵保温杯 500ml',
        content=(
            '云朵保温杯采用316不锈钢内胆，容量500ml，日常售价129元，'
            '提供奶白和雾蓝两种颜色，适合通勤和户外使用。'
            '杯身建议手洗，不建议放入洗碗机，也不建议盛放碳酸饮料。'
            '首次使用前请用温水清洗，日常清洗后请倒置晾干。'
            '商品支持7天无理由退换，非人为损坏享一年质保。'
        ),
    ),
    KnowledgeDocument(
        doc_id='product_002',
        title='AirLite 无线降噪耳机',
        content=(
            'AirLite 无线降噪耳机采用蓝牙5.3连接，单次续航8小时，'
            '配合充电盒总续航35小时，售价299元，支持IPX5防水。'
            '首次配对时打开充电盒并长按功能键3秒，在手机蓝牙列表中选择 AirLite。'
            '清洁时请使用干燥软布擦拭耳机表面，避免使用酒精浸泡。'
            '耳机支持7天无理由退换，质量问题一年保修。'
        ),
    ),
)
