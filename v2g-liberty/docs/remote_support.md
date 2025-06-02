# V2G Liberty remote support

We can help you with any V2G Liberty problems, we do this remote over the internet.

To arrange access for remote support the V2G Liberty team uses the Tailscale add-on. This has the advantage of being free, secure, private and under your control. You can also use this for accessing your HA instance from outside your home-network.

If you feel the makers of Home Assistant deserve some gratitude ($$), consider remote access through Home Assistant Cloud (HAC). In your Home Assistant go to `Settings -> Home Assistant Cloud`, you can get a 1-month free trial. If you use HAC then leave out the Tailscale steps from this step-by-step guide.

There are other ways to get remote access but to keep things simple we stick to Tailscale here.

In overview this is how:

1. Add an HA account for V2G Liberty support

2. Install and set-up Tailscale

3. In Tailscale cloud share your device (=create a share link)

4. Send the HA account and Tailscale share link to us

5. Remote support session with the V2G Liberty team 

6. Withdraw access (temporarily)

We provide this service only to users with a SmartSchedule subscription. You can request this at [https://v2g-liberty.eu/?pagename=contact](https://v2g-liberty.eu/?pagename=contact).

Want to know more about Tailscale? Visit their knowledge base at [https://tailscale.com/kb](https://tailscale.com/kb).

## **1. Add a Home Assistant account for V2G Liberty support**

This is needed so that it can later be revoked and so that it does not interfere with the normal way of working in HA.

1. Open Home Assistant web interface

2. Go to `Settings` -> `People` -> `Add person`

3. `Name`: v2g-liberty-support \
Set `Allow person to login` to `On`

4. `Display name`: v2g-liberty-support \
   `Username`: v2g-liberty-support \
   `Password`: **** \
Create one and write it down in your fav. Password manager (or a Word doc) \
Set `Local access only` to `Off` \
Set `Administrator` to `On`

5. Click `Create` and again `Create`

That was it for step one, easy peasy.

Keep the account data safe, it does give access to your Home Assistant!

## **2. Install and set-up Tailscale**

Tailscale is a VPN free service that makes the devices and applications you own accessible anywhere in the world, securely and effortlessly. We prefer this because it integrated in Home Assistant and is much simpler than traditional VPNs. Here we go...

1. Open Home Assistant web interface

2. Go to `Settings` -> `Add-ons` -> `Add-on Store`

3. Search for Tailscale and select it

4. Click `INSTALL`

5. None of the settings `Start on boot`, `watch dog` or `Show in sidebar` are needed. \
However, if you intend to use it for remote access for yourself, the first two are useful.

6. Click `START` and the `OPEN WEB UI`

7. Sometimes a message appears about an expired key, then just hit `Re-authenticate`

8. Login to connect \
   - Tailscale uses other cloud services (Google, Microsoft, Apple, etc.) for login on a Tailscale account, so you are presented a list of so called identity providers (ID-P’s). \
   - You most likely do not have a Tailscale account yet, no problem, just use your favourite ID-P to login and an account is created automatically. \
   - If you don’t have an account for any of the ID-P’s we suggest you create a Github account at https://github.com/join. Do this before you proceed here.\
   - It is advisable to use a private account for the ID-P's, do not use a work account for this.
   - If you do not get acces ("Login not possible"), you can try the following:
       * In Home Assistant, go to `Settings` > `Add-ons` > `Tailscale`.
       * Open the `Log` (top-right corner).
       * Wait for the logs to load, then look for a Tailscale login URL (starts with `https://login.tailscale.com`).
       * Copy the link, open it in your browser, and re-authenticate.

9. Once you have logged in your asked to connect the device. Do this by clicking the button `Connect`

10. You are presented with a "success" message.

Tada! You have done the hardest part, your device (Home Assistant) is connected to your personal "tailnet".

11. After a while you are automatically redirected to the Tailscale cloud. \
Answer the questions to proceed.

12. You get a suggestion to add a second device. \
That would be most likely be your mobile phone. When you add this you can use the Home Assistant companion app on your phone to control HA (and thus V2G Liberty) even when your phone is not connected to your home WiFi. \
You can do this now or later (click the link `Skip this introduction` on the bottom of the page, you’ll be taken to the Tailscale cloud).

This was perhaps a little more challenging, lucky for you, from here on it is easy!

## **3. Create a share link for the device**

For us at V2G Liberty to access your HA machine via Tailscale there is one more step to take, you have to share the device with us, here’s how:

1. If not still logged in from step 2-12, login again to Tailscale cloud at [https://tailscale.com/login](https://tailscale.com/login), again with the ID-P you used earlier.

2. Go to the `Machines` tab, you’ll see your Home Assistant machine (as the only one) in the list.

3. In the row on the right hover/click the menu link (three dots) and then select `Share...`.

4. Click the `Copy share link` tab (do not use the e-mail option here) and then the button `Copy share link`.

5. Go your password manager (or Word doc) again (from step 1-4) and paste the share link, it should look like this: \
`https://login.tailscale.com/admin/invite/XxXxXxXxX` (Where XxXx = a code with mixed case letters and numbers)

That was just clicking and coping, no sweat! Keep the share link safe, it does give access to your Home Assistant machine.

You can ignore the `Exit node` and `Subnets` stuff, they are not needed.

6. Tip: I is advisable to disable key expiry: \
   In the tailscale.com `Machines` tab, find your machine and click the three dots next to it and select `Disable key expiry`.

## **4. Send the HA account and Tailscale share link to us**

We prefer sending the data via Signal or Whatsapp as they have encryption. In the invoice for the SmartSchedules you can find contact info for both these platforms.

Alternatively send it in an e-mail to [support@v2g-liberty.eu](http://support@v2g-liberty.eu)

Ofcourse, we need to know what problems you are running into, so add these in as much detail as possible.

We normally like the session to be a live video call as well so we can ask questions and tell you what we are doing. So please add some suggestions for date/times.

We’ll reply with a specific date/time that suits us.

## **5. Remote support session by V2G Liberty support**

We’ll send you a link for the video call a day or so in advance. Be sure to try it out before the meeting.

We’ll log in to your machine, and together we’ll fix the problem. Usually within 15 minutes or so.

## **6. Withdraw access (temporarily)**

If you would like to stop the access for V2G Liberty support it is easy to do:

1. In Home Assistant go to `Setting -> Add-ons -> Tailscale`

2. Select `STOP`

That’s it! Every quick and easy, but... Now you can also not use Tailscale anymore from your devices (if you have any that connect). So, maybe you’d like another way, read on...

3. In Home Assistant go to `Settings -> People` and select `v2g-liberty-support`

4. In the pop-up set the `Allow person to login` toggle to `Off`, you’ll are warned that this will delete the user. Go ahead and click `Delete`.

This is sufficient by itself, but you can go a bit further for more security:

5. Login to the Tailscale cloud at [https://tailscale.com/login](https://tailscale.com/login), again with the ID-P you used earlier.

6. Go to `Machines` and in the row of your Home Assistant machine click the menu link (three dots) and select `Share...`

7. In the bottom of the pop-up you’ll see the share link you created earlier. Click the menu link (three dots) and select `Revoke invite`

