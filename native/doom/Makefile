# Builds doomgeneric with our flipdot platform file (no X11/SDL, no sound).
#   make DG=<path to doomgeneric's "doomgeneric" source folder>
# scripts/setup-doom.sh fetches the pinned doomgeneric and runs this.

DG ?= build/doomgeneric-src/doomgeneric
OBJDIR = build/obj
OUTPUT = build/doomgeneric_flipdot

CC ?= gcc
CFLAGS ?= -O2
CFLAGS += -w -DNORMALUNIX -DLINUX -D_DEFAULT_SOURCE -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 -I$(DG)
LDFLAGS += -Wl,--gc-sections
LIBS = -lm

SRC_DOOM = dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale \
	f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio \
	m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor \
	p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept \
	p_tick p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff \
	s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric
OBJS = $(addprefix $(OBJDIR)/, $(addsuffix .o, $(SRC_DOOM))) $(OBJDIR)/doomgeneric_flipdot.o

all: $(OUTPUT)

$(OUTPUT): $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) $(OBJS) -o $@ $(LIBS)

$(OBJS): | $(OBJDIR)

$(OBJDIR):
	mkdir -p $(OBJDIR)

$(OBJDIR)/%.o: $(DG)/%.c
	$(CC) $(CFLAGS) -c $< -o $@

$(OBJDIR)/doomgeneric_flipdot.o: doomgeneric_flipdot.c
	$(CC) $(CFLAGS) -Wall -c $< -o $@

clean:
	rm -rf $(OBJDIR) $(OUTPUT)

.PHONY: all clean
