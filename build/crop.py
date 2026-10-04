from PIL import Image
im = Image.open(r"build\shot_video.png")
# 预览条区域
crop = im.crop((588, 858, 1110, 912)).resize((522*3, 54*3), Image.NEAREST)
crop.save(r"build\crop_preview.png")
print("saved", crop.size)
