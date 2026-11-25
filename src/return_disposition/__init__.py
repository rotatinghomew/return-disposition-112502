"""退货原因的可比归因与仓配处置。

- input_contract：原始资料读取边界；
- dictionary / catalog：原因字典与商品规格的版本化解释；
- evidence：自由文本、标准原因、质检结论三层证据的归因；
- cohorts / metrics：可比队列与指标（分布、复购、结构校正、无法归因）；
- disposition：容量与承诺约束下的处置建议与重计划；
- trace / pipeline：建议的追溯回查与整体编排。
"""

from .input_contract import load_bundle
from .pipeline import Report, format_report, run

__all__ = ["load_bundle", "run", "Report", "format_report"]
