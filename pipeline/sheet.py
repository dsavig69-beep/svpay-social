import sys
from PIL import Image
D=sys.argv[1]; pre=sys.argv[2]; out=sys.argv[3]; w=int(sys.argv[4]) if len(sys.argv)>4 else 432
h=int(w*1.25)
ims=[Image.open(f'{D}/{pre}{i}.png').convert('RGB').resize((w,h),Image.LANCZOS) for i in range(1,7)]
s=Image.new('RGB',(w*3+20,h*2+10),(255,255,255))
for k,im in enumerate(ims): s.paste(im,((k%3)*(w+10),(k//3)*(h+10)))
s.save(f'{D}/{out}',quality=90)
