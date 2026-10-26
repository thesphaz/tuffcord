SUFCORD — HALLOWEEN EDITION

RUNNING THE GAME
1. Install Python 3.10 or newer.
2. Open a terminal in the MyGame folder.
3. Run: python -m pip install -r requirements.txt
4. Run: python app.py
5. On the host computer, open http://127.0.0.1:5000
6. Other devices on the same network can open http://10.224.173.55:5000 if that is still the host computer's LAN IP and the firewall allows port 5000.

ROLE CODES
Owner code: Zachary1432S
Moderator/Admin code: MrTinyToon

Owner and moderator/admin roles are separate from visible tags. Turning a tag off does not remove the underlying permissions. Users can toggle off their own visible standard tags in the lobby, and everyone can toggle off custom tags assigned to them.

CHAT COMMANDS
/give tag USER TAGNAME COLOUR
 Gives a custom tag to a player in your current room. Only the owner and staff can use this command.
 Example: /give tag bob Pumpkin orange
 Example: /give tag bob Pumpkin #ff873b

/tagoff USER TAG
 Owner only. Disables a player's visible tag.
 Example: /tagoff bob VIP

/ban USER REASON TIME
 Globally bans the username from all rooms on this sufcord server. Only the owner and admins/moderators can use this command. A reason and duration are required.
 Time formats: 30s, 10m, 2h, 7d, 1w, permanent
 Example: /ban TheCoolGuy repeated spam 1d
 Example: /ban TheCoolGuy severe rule breaking permanent

/unban USER
 Removes a global ban. Only the owner and admins/moderators can use this command.
 Example: /unban TheCoolGuy

/mute USER [SECONDS]
 Mutes a player in the current room. The room host and staff can use it. Default: 300 seconds; maximum: 3600 seconds.

/unmute USER
 Removes a manual mute in the current room.

/help
 Shows the in-game command summary.

USERNAMES AND AUTOMODERATION
- Usernames must be one word, up to 20 characters, using letters, numbers, or underscores only.
- Usernames containing detected swearing, slurs, NSFW terms, DIH, or other blocked terms are rejected.
- Detected swearing and slurs are censored and trigger an automatic 60-second mute.
- Keyword filters are imperfect and cannot catch every possible harmful term or spelling variation.

STORAGE AND LIMITATIONS
- Global bans are saved in roomspace.db and survive server restarts on this computer.
- Global bans apply to every room hosted by this copy of sufcord. Separate machines/deployments do not share bans unless they share the same database.
- Global bans are username-based, not account-based. A person may evade a username-only ban by choosing a different allowed name; account authentication would be needed to prevent that reliably.
- Global bans and chat mutes are saved in roomspace.db. They survive page refreshes and server restarts on this computer.
- Room lists and active room membership are held in memory; rooms disappear when everyone leaves or the server restarts.
- Staff unlocks and owner-issued custom tags are held in session/in-memory storage and may need to be set again after a server restart.
- Image/GIF sharing uses direct links, not file uploads.
