from collections.abc import Mapping

type Scalar = None | bool | int | str
type ComponentValue = Scalar | tuple[Scalar, ...]

type ComponentDict = Mapping[str, ComponentValue]
type StateDict = Mapping[str, ComponentDict]
