from __future__ import unicode_literals
import ConfigParser
import io

sample_config = """
[display]
colormap3d: gray
"""

cfg = ConfigParser.SafeConfigParser()
cfg.readfp(io.BytesIO(sample_config))
