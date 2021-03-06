#!/usr/bin/python
#
# Copyright 2011 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import glob
import pygame
import sys
import os.path
import urllib2
import BaseHTTPServer
import mmap
import cStringIO
import base64
import subprocess
import re

paused = False           # For image inspection
image_number = None      # Scanimage starts counting at 1
book_dimensions = None   # (top, bottom, side) in pixels
fullscreen = True        # Easier to debug in a window
suppressions = set()     # Pages we don't want to keep
left_offset = 593        # Hardware sensor position in pixels
right_offset = 150       # Hardware sensor position in pixels
dpi = 300                # Hardware resolution

def blue():
  """Original scansation blue, handed down from antiquity."""
  return (70, 120, 173)

def clearscreen(screen):
  """And G-d said, 'Let there be blue light!'"""
  screen.fill(blue())

def get_epsilon(screen):
  """How much to separate page image from center of display."""
  return screen.get_width() / 100

def render_text(screen, msg, position):
  """Write messages to screen, such as the image number."""
  pos = [0, 0]
  font = pygame.font.SysFont('Courier', 28, bold=True)
  color = blue()
  if image_number in suppressions:
    color = pygame.Color('red')
  for line in msg.split("\n"):
    if len(line) > 0:
      text = font.render(line.rstrip('\r'), 1, (255, 255, 255))
      background = pygame.Surface(text.get_size())
      background.fill(color)
      if position == "upperright":
        pos[0] = screen.get_width() - text.get_width()
      screen.blit(background, pos)
      screen.blit(text, pos)
      pygame.display.update(pygame.Rect(pos, text.get_size()))
    pos[1] += 30
    color = blue()

def read_ppm_header(fp, filename):
  """Read dimensions and headersize from a PPM file."""
  headersize = 0
  magic_number = fp.readline()
  if magic_number != "P6\n":
    raise TypeError("Hey! Not a ppm image file: %s" % filename)
  headersize += len(magic_number)
  comment = fp.readline()
  if comment[0] == "#":
    headersize += len(comment)
    dimensions = fp.readline()
  else:
    dimensions = comment
  headersize += len(dimensions)
  max_value = fp.readline()
  if max_value != "255\n":
    raise ValueError("I only work with 8 bits per color channel")
  headersize += len(max_value)
  w = int(dimensions.split(" ")[0])
  h = int(dimensions.split(" ")[1])
  return (w, h), headersize

def scale_to_crop_coord(scale_coord, scale_size, crop_size, epsilon):
  """Scale images are displayed 2-up in the screen."""
  w2 = pygame.display.Info().current_w // 2
  is_left = scale_coord[0] < w2
  if is_left:
    x0 = w2 - epsilon - scale_size[0]
  else:
    x0 = w2 + epsilon
  x = (scale_coord[0] - x0) * crop_size[0] // scale_size[0]
  y = scale_coord[1] * crop_size[1] // scale_size[1]
  if is_left:
    x = crop_size[0] - x
  return (x, y), is_left

def crop_to_full_coord(crop_coord, is_left):
  """We always crop out saddle, and usually crop to book page."""
  x, y = crop_coord
  if book_dimensions:
    (top, bottom, side) = book_dimensions
    y += top
  if is_left:
    y += left_offset
  else:
    y += right_offset
  return x, y

def process_image(h, filename, is_left):
  """Return both screen resolution and scan resolution images."""
  kSaddleHeight = 3600  # scan pixels
  f = open(filename, "r+b")
  dimensions, headersize = read_ppm_header(f, filename)
  map = mmap.mmap(f.fileno(), 0)
  image = pygame.image.frombuffer(buffer(map, headersize), dimensions, 'RGB')
  unused, y = crop_to_full_coord((0, 0), is_left)
  if book_dimensions:
    (top, bottom, side) = book_dimensions
    wh = (side, bottom - top)
  else:
    wh = (image.get_width(), kSaddleHeight)
  rect = pygame.Rect((0, y), wh)
  crop = image.subsurface(rect)
  w = image.get_width() * h // kSaddleHeight
  scale = pygame.transform.smoothscale(crop, (w, h))
  if is_left:
    scale = pygame.transform.flip(scale, True, False)
  return scale, crop

def clip_image_number(playground):
  """Only show images that exist."""
  global image_number
  if image_number < 1:  # scanimage starts counting at 1
    image_number = 1
  while image_number > 1:
    filename = os.path.join(playground, '%06d.pnm' % image_number)
    if os.path.exists(filename):
      break
    image_number -= 2

def get_book_dimensions(playground):
  """User saved book dimensions in some earlier run."""
  global book_dimensions
  try:
    for line in open(os.path.join(playground, "book_dimensions")).readlines():
      if line[0] != "#":
        book_dimensions = [int(x) for x in line.split(",")]
  except IOError:
    pass

def unset_book_dimensions(playground):
  global book_dimensions
  if book_dimensions:
    book_dimensions = None
    os.unlink(os.path.join(playground, "book_dimensions"))

def set_book_dimensions(click, epsilon, crop_size, scale_size, playground):
  """User has dragged mouse to specify book position in image."""
  global book_dimensions
  down = list(click[0])
  up = list(click[1])
  min_book_dimension = 30  # screen pixels
  w2 = pygame.display.Info().current_w // 2
  down[0] = abs(w2 - down[0]) + w2
  up[0] =  abs(w2 - up[0]) + w2
  if min(abs(down[1] - up[1]), abs(up[0] - w2)) < min_book_dimension:
    return
  side = max(down[0], up[0]) - w2 - epsilon
  top = min(down[1], up[1])
  bottom = max(down[1], up[1])
  side = min(side, scale_size[0])
  side = side * crop_size[0] // scale_size[0]
  top = top * crop_size[1] // scale_size[1]
  bottom = bottom * crop_size[1] // scale_size[1]
  book_dimensions = (top, bottom, side)
  f = open(os.path.join(playground, "book_dimensions"), "wb")
  f.write("#top,bottom,side\n%s,%s,%s\n" % book_dimensions)
  f.close()

def zoom(screen, click, scale_a, scale_b, crop_a, crop_b):
  """Given a mouseclick, zoom in on the region."""
  coord, is_left = scale_to_crop_coord(click, scale_a.get_size(),
                                       crop_a.get_size(), get_epsilon(screen))
  if is_left:
    crop = crop_a
  else:
    crop = crop_b
  size = pygame.display.Info().current_w // 3
  dst = (click[0] - size, click[1] - size)
  rect = pygame.Rect((coord[0] - size, coord[1] - size), (2 * size, 2 * size))
  if is_left:
    tmp = pygame.Surface((2 * size, 2 * size))
    tmp.blit(crop, (0,0), rect)
    tmp2 = pygame.transform.flip(tmp, True, False)
    screen.blit(tmp2, dst)
  else:
    screen.blit(crop, dst, rect)

def draw(screen, image_number, scale_a, scale_b, paused):
  """Draw the page images on screen."""
  w2 = screen.get_width() // 2
  render_text(screen, "%s" % str(image_number).ljust(4), "upperleft")
  render_text(screen, "%s" % str(image_number + 1).rjust(4), "upperright")
  epsilon = get_epsilon(screen)
  render_text(screen, "\n     ", "upperleft")
  screen.blit(scale_a, (w2 - scale_a.get_width() - epsilon, 0))
  screen.blit(scale_b, (w2 + epsilon, 0))
  if paused:
    render_text(screen, "\nPAUSE", "upperleft")

def create_new_pdf(playground, width, height):
  import reportlab.rl_config
  from reportlab.pdfgen.canvas import Canvas
  pdf = Canvas(os.path.join(playground, "book.pdf"),
               pagesize=(width, height), pageCompression=1)
  pdf.setCreator('cheesegrater')
  pdf.setTitle(os.path.basename(playground))
  load_font()
  return pdf

def export_pdf(playground, screen):
  """Create a PDF file fit for human consumption"""
  if book_dimensions == None:
    return
  width = book_dimensions[2] * 72 / dpi
  height = (book_dimensions[1] - book_dimensions[0]) * 72 / dpi
  pdf = create_new_pdf(playground, width, height)
  jpegs = glob.glob(os.path.join(playground, '*.jpg'))
  jpegs.sort(reverse=True)  # Switch to reading order
  counter = 0
  for jpeg in jpegs:
    msg = "Exporting PDF %d/%d" % (counter, len(jpegs) - 1)
    render_text(screen, msg, "upperright")
    counter += 1
    number = int(os.path.basename(jpeg).split('-')[0])
    if number in suppressions or (number - 1) in suppressions:
      continue
    pdf.drawImage(jpeg, 0, 0, width=width, height=height)
    add_text_layer(pdf, jpeg, height)
    pdf.showPage()
  pdf.save()
  render_text(screen, " " * len(msg), "upperright")

def add_text_layer(pdf, jpeg, height):
  """Draw an invisible text layer for OCR data"""
  from xml.etree.ElementTree import ElementTree, ParseError
  p = re.compile('bbox((\s+\d+){4})')
  hocrfile = os.path.splitext(jpeg)[0] + ".html"
  hocr = ElementTree()
  try:
    hocr.parse(hocrfile)
  except ParseError:
    print("Parse error for %s" % hocrfile)  # Tesseract bug fixed Aug 16, 2012
    return
  except IOError:
    return  # Tesseract not installed; user doesn't want OCR
  for line in hocr.findall(".//%sspan"%('')):
    if line.attrib['class'] != 'ocr_line':
      continue
    coords = p.search(line.attrib['title']).group(1).split()
    # Heuristic - we assume 30% of line bounding box is descenders
    b = float(coords[3]) - 0.3 * (float(coords[3]) - float(coords[1]))
    base = height - b * 72 / dpi
    for word in line:
      if word.attrib['class'] != 'ocr_word' or word.text is None:
        continue
      default_width = pdf.stringWidth(word.text.strip(), 'invisible', 8)
      if default_width <= 0:
        continue
      coords = p.search(word.attrib['title']).group(1).split()
      left = float(coords[0]) * 72 / dpi
      right = float(coords[2]) * 72 / dpi
      text = pdf.beginText()
      text.setTextRenderMode(3)  # double invisible
      text.setFont('invisible', 8)
      text.setTextOrigin(left, base)
      text.setHorizScale(100.0 * (right - left) / default_width)
      text.textLine(word.text.strip())
      pdf.drawText(text)

def save_jpeg(screen, crop_a, crop_b, playground, image_number):
  """Save cropped images in reading order."""
  a = pygame.transform.flip(crop_a, True, False)
  p1 = write_jpeg(screen, playground, a, image_number)
  p2 = write_jpeg(screen, playground, crop_b, image_number + 1)
  return (p1, p2)

def write_jpeg(screen, playground, img, number):
  """Write JPEG image if not already there, plus remove any old cruft"""
  if book_dimensions:
    d = book_dimensions
    stem = "%06d-%s-%s-%s" % (number, d[0], d[1], d[2])
  else:
    return
  hocrs = glob.glob(os.path.join(playground, '%06d-*.html' % number))
  jpegs = glob.glob(os.path.join(playground, '%06d-*.jpg' % number))
  for file in hocrs + jpegs:
    if os.path.splitext(os.path.basename(file))[0] != stem:
      os.remove(file)
  jpeg = os.path.join(playground, stem + ".jpg")
  if not os.path.exists(jpeg):
    pygame.image.save(img, jpeg)
  p = None
  hocr = os.path.join(playground, stem)
  if os.path.exists(hocr + ".html"):
    msg = "\n\n\n   "
  else:
    msg = "\n\n\nOCR"
    try:
      p = subprocess.Popen(['tesseract', jpeg, hocr, 'hocr'])
    except OSError:
      pass  # Tesseract not installed; user doesn't want OCR
  if number % 2 == 0:
    render_text(screen, msg,  "upperleft")
  else:
    render_text(screen, msg,  "upperright")
  return p

def get_bibliography(barcode):
  """Hit up Google Books for bibliographic data. Thanks, Leonid."""
  if barcode[0:3] == "978":
    url = ("http://books.google.com/books/download/"
           "?vid=isbn%s&output=enw&source=cheese" % barcode[0:13])
    try:
      bib = urllib2.urlopen(url, None, 2).read()
    except urllib2.URLError, e:
      if hasattr(e, 'reason'):
        excuse =  e.reason
      elif hasattr(e, 'code'):
        excuse = "HTTP return code %d\n" % e.code
        excuse += BaseHTTPServer.BaseHTTPRequestHandler.responses[e.code][0]
      return "Error looking up barcode: %s\n\n%s" % (barcode.split("_")[0],
                                                     excuse)
    return bib
  return "Unknown Barcode: %s" % barcode.split("_")[0]

def splashscreen(screen, barcode):
  """Like opening credits in a movie, but more useful."""
  clearscreen(screen)
  render_text(screen, "Looking up barcode: %s" % barcode.split("_")[0],
              "upperleft")
  clearscreen(screen)
  render_text(screen, get_bibliography(barcode), "upperleft")
  pygame.display.update()
  render_text(screen, ("\n\n\n\n\n\n\n\n\n\n"
                       "H,?                  = help\n"
                       "MOUSE                = crop | mosaic | zoom\n"
                       "ARROWS               = navigation\n"
                       "PgUp/PgDn/Home/End   = navigation!\n"
                       "\n"
                       "S                    = screenshot\n"
                       "Q,ESC                = quit\n"
                       "\n"
                       "E                    = export to pdf\n"
                       "DELETE,BACKSPACE     = delete\n"
                       "U                    = uncrop\n"
                       "F11,F                = fullscreen\n"
                       "P,SPACE              = pause\n"
                       ), "upperleft")
  clearscreen(screen)
  pygame.time.wait(2000)

def get_suppressions(playground):
  """Read list of suppressed images from file"""
  global suppressions
  try:
    for line in open(os.path.join(playground, "suppressions")).readlines():
      if line[0] != "#":
        suppressions = set([int(x) for x in line.split(",")])
  except IOError:
    pass

def set_suppressions(playground, image_number):
  """Toggle supression for the supplied image pair, persistantly"""
  global suppressions
  if image_number in suppressions:
    suppressions.remove(image_number)
  else:
    suppressions.add(image_number)
  filename = os.path.join(playground, "suppressions")
  f = open(filename, "wb")
  f.write("#Suppressed image pairs indicated by left image number\n")
  f.write(str(suppressions).strip('set([])'))
  f.write("\n");
  f.close()

def handle_key_event(screen, event, playground, barcode, mosaic_click,
                     fullsize):
  """I find it easier to deal with keystrokes mostly in one place."""
  global image_number
  global paused
  global fullscreen
  newscreen = None
  if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
    pygame.quit()
    sys.exit()
  elif event.key == pygame.K_SPACE or event.key == pygame.K_p:
    paused = not paused
  elif event.key == pygame.K_e:
    export_pdf(playground, screen)
  elif event.key == pygame.K_DELETE or event.key == pygame.K_BACKSPACE:
    set_suppressions(playground, image_number)
  elif event.key == pygame.K_F11 or event.key == pygame.K_f:
    fullscreen = not fullscreen
    set_pygame_window(fullsize, fullscreen)
    newscreen = pygame.display.get_surface()
    clearscreen(newscreen)
  elif event.key == pygame.K_LEFT or event.key == pygame.K_UP:
    image_number -= 2
    paused = True
  elif event.key == pygame.K_RIGHT or event.key == pygame.K_DOWN:
    image_number += 2
    paused = True
  elif event.key == pygame.K_PAGEUP:
    image_number -= 10
    paused = True
  elif event.key == pygame.K_PAGEDOWN:
    image_number += 10
    paused = True
  elif event.key == pygame.K_HOME:
    image_number = 1
    paused = True
  elif event.key == pygame.K_END:
    pnms = glob.glob(os.path.join(playground, '*.pnm'))
    pnms.sort(reverse=True)
    candidate = int(os.path.splitext(os.path.basename(pnms[0]))[0])
    image_number = candidate - 1 + candidate % 2  # left page
    paused = True
  elif event.key == pygame.K_u:
    unset_book_dimensions(playground)
  elif event.key == pygame.K_s:
    filename = "screenshot-" + barcode + "-" + str(image_number) + ".jpg"
    pygame.image.save(screen, filename);
    render_text(screen, filename, "upperright")
    pygame.time.wait(2000)
    render_text(screen, " " * len(filename), "upperright")
  elif event.key == pygame.K_h or event.key == pygame.K_QUESTION:
    splashscreen(screen, barcode)
    pygame.time.wait(3000)
  clip_image_number(playground)
  if mosaic_click:
    clearscreen(screen)
  return newscreen

def render(playground, screen, paused, image_number):
  """Calculate and draw entire screen, including book images."""
  filename_a = os.path.join(playground, '%06d.pnm' % image_number)
  filename_b = os.path.join(playground, '%06d.pnm' % (image_number + 1))
  h = screen.get_height()
  scale_a, crop_a = process_image(h, filename_a, True)
  scale_b, crop_b = process_image(h, filename_b, False)
  draw(screen, image_number, scale_a, scale_b, paused)
  pygame.display.set_caption("%d %s" %
                             (image_number, os.path.basename(playground)))
  pygame.display.update()
  return crop_a, crop_b, scale_a, scale_b, image_number

def mosaic_dimensions(screen):
  """Reduce some cut-n-past code."""
  columns = 10
  rows = 20
  h = screen.get_height() // rows
  size = (2 * h, h)
  windowsize = 2 * rows * columns
  start = max(1, image_number - windowsize + 2)
  return size, windowsize, start, columns

def navigate_mosaic(playground, screen, click):
  """Click on a mosaic tile, jump to that page."""
  global image_number
  clearscreen(screen)
  size, unused, start, columns = mosaic_dimensions(screen)
  if click[0] > columns * size[0]:
    return
  x, y = click[0] // size[0], click[1] // size[1]
  candidate = start + 2 * (columns * y + x)
  filename = os.path.join(playground, '%06d.pnm' % candidate)
  if os.path.exists(filename):
    image_number = candidate

def render_mosaic(screen, playground, click, scale_size, crop_size,
                  image_number):
  """Useful for seeing lots of page numbers at once."""
  crop_coord, is_left = scale_to_crop_coord(click, scale_size,
                                            crop_size, get_epsilon(screen))
  full_coord = crop_to_full_coord(crop_coord, is_left)
  size, windowsize, start, columns = mosaic_dimensions(screen)
  if not is_left:
    start += 1
  for i in range(start, start + windowsize, 2):
    x = ((i - start) // 2) % columns
    y = ((i - start) // 2) // columns
    filename = os.path.join(playground, '%06d.pnm' % i)
    if not os.path.exists(filename):
      break
    f = open(filename, "r+b")
    map = mmap.mmap(f.fileno(), 0)
    dimensions, headersize = read_ppm_header(f, filename)
    image = pygame.image.frombuffer(buffer(map, headersize), dimensions, 'RGB')
    src = full_coord[0] - 3 * size[0] // 2, full_coord[1] - 3 * size[1] // 2
    rect = pygame.Rect(src, (size[0] * 3, size[1] * 3))
    crop = image.subsurface(rect)
    scale = pygame.transform.smoothscale(crop, size)
    if is_left:
      scale = pygame.transform.flip(scale, True, False)
    dst = (size[0] * x, size[1] * y)
    dirty = pygame.Rect(dst, size)
    if is_left:
      left_image_number =  i
    else:
      left_image_number =  i - 1
    screen.blit(scale, dst)
    if left_image_number in suppressions:
      red = pygame.Surface(scale.get_size())
      red.fill(pygame.Color('red'))
      red.set_alpha(128)
      screen.blit(red, dst)
    map.close()
    f.close()
    pygame.display.update(dirty)

def get_beep():
  """Not having as external file makes life easier for sysadmins."""
  wav = """
UklGRigCAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQQCAAA01jVaF1f2WGJW1FoN
U5hhMcMxoKCoiqhcogiwOJTK/zxqak9fXHxWrFYcXv08rZtQrhShk6wBoXaudZtGPcZdBlcnVqdc
N09Tatf//pN7sKKhmKkypwui3sBzZJhP+V5tUfBeq09XZALB36Fnp1mp7KEksGWUXv/eappOVV1n
VdhX41w7Pm6ai6/hn7yt5Z+Cr3uaLD71XMJXgVU4XbpOu2qE/z2UT7C9oYupM6cVosvAjGR5Tx9f
QVEgX3hPimTQwA+iOqeEqcKhS7BBlIH/vmq2TjxdfVXGV/FcMD53moWv45+8reOfhq92mjE+71zI
V3xVPF22Tr9qf/9ClEuwwqGFqTinEKLQwItkd08gX0JRHl96T4pkzsASojenhanDoUuwQJSC/7xq
uE48XXxVx1fxXC4+epqCr+afuq3kn4Wvd5oxPu9cx1d9VTtduE6+an7/RZRGsMihfqk/pwui1MCG
ZHxPG19HURpffU+HZNHAEKI4p4apwKFPsDuUh/+2asJOLl2NVbNXBV0cPouaca/5n6Wt+p9wr4ma
Ij77XMFXfFVDXalO0mpm/2OUILD2oUipgKe+oSzBImTtT6FeyFGTXghQ+mNgwYGhw6cCqTmi5K+W
lED/5WqsTiddtVVnV3pdej1bm3OuJKFNrH+hv61knCI8GF+PVbVXG1uUUIZp4f4=
"""
  f = cStringIO.StringIO(base64.decodestring(wav))
  return pygame.mixer.Sound(f)

def set_pygame_window(size, fullscreen):
  """Apply fullscreen or windowed user interface"""
  if fullscreen:
    return pygame.display.set_mode(size, pygame.FULLSCREEN)
  pygame.display.set_mode((size[0] // 2, size[1] // 2), pygame.RESIZABLE)

def main(argv1):
  """Display scanned images as they are created."""
  playground = argv1.rstrip('/')
  barcode = os.path.basename(playground)
  pygame.init()
  global image_number
  global paused
  global book_dimensions
  last_drawn_image_number = 0
  get_book_dimensions(playground)
  get_suppressions(playground)
  try:
    beep = get_beep()
  except:
    pass
  fullsize = (pygame.display.Info().current_w, pygame.display.Info().current_h)
  set_pygame_window(fullsize, fullscreen)
  screen = pygame.display.get_surface()
  pygame.display.set_caption("%s" % os.path.basename(playground))
  splashscreen(screen, barcode)
  scale_a = None  # prevent crash if keypress during opening splashscreen
  image_number = 1
  pygame.time.set_timer(pygame.USEREVENT, 50)
  shadow = pygame.Surface(screen.get_size())
  shadow.set_alpha(128)
  shadow.fill((0, 0, 0))
  mosaic_click = None  # Don't mode me in, bro!
  p1, p2 = None, None
  busy = False
  while True:
    for event in [ pygame.event.wait() ]:
      if event.type == pygame.MOUSEBUTTONDOWN:
        busy = True
        pygame.event.clear(pygame.USEREVENT)
      if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        if mosaic_click or book_dimensions:
          continue
        leftdownclick = event.pos
        oldscreen = screen.copy()
        shadowscreen = screen.copy()
        shadowscreen.blit(shadow, (0, 0))
        screen.blit(shadowscreen, (0, 0))
        prevroi = pygame.Rect(event.pos, (0, 0))
        pygame.display.update()
      elif event.type == pygame.MOUSEMOTION and event.buttons[0] == 1:
        if book_dimensions or mosaic_click:
          continue
        x = abs(event.pos[0] - screen.get_width() // 2)
        pos = (screen.get_width() // 2 - x, min(leftdownclick[1], event.pos[1]))
        roi = pygame.Rect(pos, (2 * x, abs(leftdownclick[1] - event.pos[1])))
        dirty = roi.union(prevroi)
        prevroi = roi.copy()
        screen.blit(shadowscreen, dirty.topleft, area = dirty)
        screen.blit(oldscreen, roi.topleft, area = roi)
        pygame.display.update(dirty)
      elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        if mosaic_click:
          navigate_mosaic(playground, screen, event.pos)
          last_drawn_image_number = None
          mosaic_click = None
        elif not book_dimensions:
          oldscreen = None
          leftclick = (leftdownclick, event.pos)
          set_book_dimensions(leftclick, get_epsilon(screen), crop_a.get_size(),
                              scale_a.get_size(), playground)
          clearscreen(screen)
          crop_a, crop_b, scale_a, scale_b, last_drawn_image_number = \
              render(playground, screen, paused, image_number)
        busy = False
      elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
        draw(screen, image_number, scale_a, scale_b, paused)
        zoom(screen, event.pos, scale_a, scale_b, crop_a, crop_b)
        pygame.display.update()
      elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
        mosaic_click = None
        clearscreen(screen)
        draw(screen, image_number, scale_a, scale_b, paused)
        pygame.display.update()
        busy = False
      elif event.type == pygame.MOUSEBUTTONUP and event.button == 2:
        mosaic_click = event.pos
        if image_number != last_drawn_image_number:
          crop_a, crop_b, scale_a, scale_b, last_drawn_image_number = \
              render(playground, screen, paused, image_number)
        try:
          render_mosaic(screen, playground, mosaic_click, scale_a.get_size(),
                        crop_a.get_size(), image_number)
        except ValueError:
          mosaic_click = None
        paused = True
        busy = False
      elif event.type == pygame.QUIT:
        pygame.quit()
        sys.exit()
      elif mosaic_click and \
            event.type == pygame.KEYDOWN and \
            (event.key == pygame.K_PAGEUP or event.key == pygame.K_PAGEDOWN):
        unused, windowsize, start, unused = mosaic_dimensions(screen)
        if event.key == pygame.K_PAGEUP:
          image_number = start - 2
        elif event.key == pygame.K_PAGEDOWN:
          image_number = start + windowsize + windowsize - 2
        clip_image_number(playground)
        clearscreen(screen)
        if image_number != last_drawn_image_number:
          crop_a, crop_b, scale_a, scale_b, last_drawn_image_number = \
              render(playground, screen, paused, image_number)
        render_mosaic(screen, playground, mosaic_click, scale_a.get_size(),
                      crop_a.get_size(), image_number)
      elif event.type == pygame.KEYDOWN:
        newscreen = handle_key_event(screen, event, playground, barcode,
                                     mosaic_click, fullsize)
        mosaic_click = None
        last_drawn_image_number = None
        if newscreen:
          screen = newscreen
          clearscreen(screen)
      elif event.type == pygame.VIDEORESIZE:
        screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
        clearscreen(screen)
        last_drawn_image_number = None
      elif event.type == pygame.USEREVENT:
        if busy:
          continue
        if p1 and p1.poll() != None:
          p1 = None
          render_text(screen, "\n\n\n   ", "upperleft")
        if p2 and p2.poll() != None:
          p2 = None
          render_text(screen, "\n\n\n   ", "upperright")
        if not (paused or p1 or p2):
          image_number += 2
          clip_image_number(playground)
        if image_number != last_drawn_image_number:
          try:
            crop_a, crop_b, scale_a, scale_b, last_drawn_image_number = \
                render(playground, screen, paused, image_number)
            p1, p2 = save_jpeg(screen, crop_a, crop_b, playground, image_number)
            if not paused:
              try:
                beep.play()
              except:
                pass
          except IOError:
            pass
          pygame.event.clear(pygame.USEREVENT)

# Glyphless variation of vedaal's invisible font retrieved from
# http://www.angelfire.com/pr/pgpf/if.html, which says:
# 'Invisible font' is unrestricted freeware. Enjoy, Improve, Distribute freely
def load_font():
  font = """
AAEAAAANAIAAAwBQRkZUTVJ1oasAAAlUAAAAHEdERUYAMAAEAAAJNAAAACBPUy8yvX2H2QAAAVgAAAB
WY21hcAANA5YAAAG4AAABOmdhc3D//wADAAAJLAAAAAhnbHlmAAAAAAAAAvwAAAAAaGVhZPfIBVkAAA
DcAAAANmhoZWEKHQJZAAABFAAAACRobXR4CAAAAAAAAbAAAAAIbG9jYQAAAAAAAAL0AAAACG1heHAEY
gAxAAABOAAAACBuYW1lwzbcaAAAAvwAAAYIcG9zdP8tAJgAAAkEAAAAKAABAAAAAQAA7vU91l8PPPUA
CwgAAAAAAMxU4C4AAAAAzFTgLgAAAAAAAAAAAAAACAACAAAAAAAAAAEAAAnY+lcAQwgAAAAAAAAAAAE
AAAAAAAAAAAAAAAAAAAABAAEAAAADAAAAAAAAAAAAAgAQAC8AQgAABAwAAAAAAAAAAQH0AZAABQAIBZ
oFMwAAARsFmgUzAAAD0QBmAhIAAAIAAAAAAAAAAACgAAKvUAB4+wAAAAAAAAAASEwgIABA//8AAAXT/
lEBMwNyBA9gAAGf3/cAAAAACAAAAAAAAAAAAAADAAAAAwAAABwAAQAAAAAANAADAAEAAAAcAAQAGAAA
AAIAAgAAAAD//wAA//8AAQAAAAABBgAAAQAAAAAAAAABAgAAAAIAAAAAAAAAAAAAAAAAAAABAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAoAeY
AAQAAAAAAAAA0AGoAAQAAAAAAAQAJALMAAQAAAAAAAgAHAM0AAQAAAAAAAwAWAQMAAQAAAAAABAAJAS
4AAQAAAAAABQAwAZoAAQAAAAAABgAJAd8AAQAAAAAACgBAAmsAAwABBAMAAgAMAqwAAwABBAUAAgAQA
roAAwABBAYAAgAMAswAAwABBAcAAgAQAtoAAwABBAgAAgAQAuwAAwABBAkAAABoAAAAAwABBAkAAQAS
AJ8AAwABBAkAAgAOAL0AAwABBAkAAwAsANUAAwABBAkABAASARoAAwABBAkABQBgATgAAwABBAkABgA
SAcsAAwABBAkACgCAAekAAwABBAoAAgAMAv4AAwABBAsAAgAQAwwAAwABBAwAAgAMAx4AAwABBA4AAg
AMAywAAwABBBAAAgAOAzoAAwABBBMAAgASA0oAAwABBBQAAgAMA14AAwABBBUAAgAQA2wAAwABBBYAA
gAMA34AAwABBBkAAgAOA4wAAwABBBsAAgAQA5wAAwABBB0AAgAMA64AAwABBB8AAgAMA7wAAwABBCQA
AgAOA8oAAwABBC0AAgAOA9oAAwABCAoAAgAMA+oAAwABCBYAAgAMA/gAAwABDAoAAgAMBAYAAwABDAw
AAgAMBBQAVAB5AHAAZQBmAGEAYwBlACAAqQAgACgAeQBvAHUAcgAgAGMAbwBtAHAAYQBuAHkAKQAuAC
AAMgAwADAANQAuACAAQQBsAGwAIABSAGkAZwBoAHQAcwAgAFIAZQBzAGUAcgB2AGUAZAAAVHlwZWZhY
2UgqSAoeW91ciBjb21wYW55KS4gMjAwNS4gQWxsIFJpZ2h0cyBSZXNlcnZlZAAAaQBuAHYAaQBzAGkA
YgBsAGUAAGludmlzaWJsZQAAUgBlAGcAdQBsAGEAcgAAUmVndWxhcgAAaQBuAHYAaQBzAGkAYgBsAGU
AOgBWAGUAcgBzAGkAbwBuACAAMQAuADAAMAAAaW52aXNpYmxlOlZlcnNpb24gMS4wMAAAaQBuAHYAaQ
BzAGkAYgBsAGUAAGludmlzaWJsZQAAVgBlAHIAcwBpAG8AbgAgADEALgAwADAAIABTAGUAcAB0AGUAb
QBiAGUAcgAgADEAMwAsACAAMgAwADAANQAsACAAaQBuAGkAdABpAGEAbAAgAHIAZQBsAGUAYQBzAGUA
AFZlcnNpb24gMS4wMCBTZXB0ZW1iZXIgMTMsIDIwMDUsIGluaXRpYWwgcmVsZWFzZQAAaQBuAHYAaQB
zAGkAYgBsAGUAAGludmlzaWJsZQAAVABoAGkAcwAgAGYAbwBuAHQAIAB3AGEAcwAgAGMAcgBlAGEAdA
BlAGQAIAB1AHMAaQBuAGcAIABGAG8AbgB0ACAAQwByAGUAYQB0AG8AcgAgADUALgAwACAAZgByAG8Ab
QAgAEgAaQBnAGgALQBMAG8AZwBpAGMALgBjAG8AbQAAVGhpcyBmb250IHdhcyBjcmVhdGVkIHVzaW5n
IEZvbnQgQ3JlYXRvciA1LjAgZnJvbSBIaWdoLUxvZ2ljLmNvbQAATgBvAHIAbQBhAGwAAABvAGIAeQE
NAGUAagBuAOkAAABuAG8AcgBtAGEAbAAAAFMAdABhAG4AZABhAHIAZAAAA5oDsQO9A78DvQO5A7oDrA
AAAE4AbwByAG0AYQBsAAAATgBvAHIAbQBhAGEAbABpAAAATgBvAHIAbQBhAGwAAABOAG8AcgBtAOEAb
AAAAE4AbwByAG0AYQBsAGUAAABTAHQAYQBuAGQAYQBhAHIAZAAAAE4AbwByAG0AYQBsAAAATgBvAHIA
bQBhAGwAbgB5AAAATgBvAHIAbQBhAGwAAAQeBDEESwRHBD0ESwQ5AAAATgBvAHIAbQDhAGwAbgBlAAA
ATgBvAHIAbQBhAGwAAABOAG8AcgBtAGEAbAAAAE4AYQB2AGEAZABuAG8AAABBAHIAcgB1AG4AdABhAA
AATgBvAHIAbQBhAGwAAABOAG8AcgBtAGEAbAAAAE4AbwByAG0AYQBsAAAATgBvAHIAbQBhAGwAAAACA
AAAAAAA/ycAlgAAAAAAAAAAAAAAAAAAAAAAAAAAAAMAAAABAAIAAAAB//8AAgABAAAADgAAABgAAAAA
AAIAAQABAAIAAQAEAAAAAgAAAAAAAQAAAADG1C6ZAAAAAL9MkvAAAAAAzFTgIQ==
"""
  ttf = cStringIO.StringIO(base64.decodestring(font))
  from reportlab.pdfbase import pdfmetrics
  from reportlab.pdfbase.ttfonts import TTFont
  pdfmetrics.registerFont(TTFont('invisible', ttf))


if __name__ == "__main__":
  if len(sys.argv) == 1:
    print("Usage: %s <imgdir>\n" % os.path.basename(sys.argv[0]))
  else:
    main(sys.argv[1])
