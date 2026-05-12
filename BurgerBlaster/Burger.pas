program Burger_Blaster;

{                                                               }

{$C- }

{$i Graph.p }    { °±²Û Turbo Pascal extra graphics commands Û²±° }

const
   total = 5;
   bmake : array[1..total,0..6] of integer =
           ((3,3,1,2,0,0,0),(4,3,1,4,2,0,0),(4,3,1,5,2,0,0),
            (5,3,1,3,1,2,0),(5,3,1,4,5,2,0));

type
   stype = string[20];
   data1 = record
              shape     : array [0..150] of integer;
           end;
   data2 = record
              x,y,d,shp : integer;
           end;

var
   nfile       : text;
   sh1         : array [0..11] of data1;
   sh2         : array [1..10] of data2;
   i,r,c,tx,td,
   score,miss,
   level,
   burgeron,
   burgernum,
   shoot,sx,sy,
   burg,hotdog : integer;
   ch          : char;
   chaa        : stype;

function st(h :integer) : stype;
begin
   str(h,chaa);
   st := chaa;
end;

{$i letters.p }    { °±²Û letter and number generator Û²±° }

procedure inkey;
begin
   if keypressed
      then read(kbd,ch)
      else ch := #0;
   if (upcase(ch)='Q') and not keypressed
      then
         begin
            textmode(c80);
            textcolor(7);
            clrscr;
            halt;
         end;
end;


procedure getshapes;
begin
   assign(nfile,'burger.shp');
   reset(nfile);
   for i := 1 to 9 do
      with sh1[i] do
         begin
            read(nfile,shape[0]);
            read(nfile,shape[1]);
            read(nfile,shape[2]);
            c := (((shape[1]+3)div 4)*shape[2]*2+6)div 3;
            for r := 3 to c-1 do
               read(nfile,shape[r]);
         end;
   close(nfile);
end;


procedure titlescreen;
begin
   graphcolormode;
   palette(2);
   graphbackground(1);
   clearscreen;
   getpic(sh1[0].shape,0,0,19,4);
   getpic(sh1[11].shape,0,0,19,9);
   gotoxy(1,25);
   for r := 0 to 80 do
      for i := 192 to 199 do
         for c := 0 to 5 do
            if getdotcolor(r,i)<>0
               then draw(r*4,(i-192)*5+c,r*4+3,(i-192)*5+c,3);
   gotoxy(1,25); write(' ':10);
   putletter(110,10,3,'Burger Blaster');
   putletter(15,40,1,'                                          ');
   putletter(90,49,1,'                    ');
   putletter(90,80,2,'Press return to play');
   for i := 1 to 5 do
      with sh1[i] do
         begin
            putpic(shape,50,i*20+74);
            for r := 1 to 25 do
               putletter(r*7+63,i*20+70,4,'.');
         end;
   putpic(sh1[9].shape,80,194);
   putletter(245,90,4,'burger');
   putletter(245,110,4,'top bun');
   putletter(245,130,4,'bottom bun');
   putletter(245,150,4,'lettuce');
   putletter(245,170,4,'tomato');
   putletter(110,190,4,concat('hot dog -',#26,' extra points'));
   repeat
      inkey;
   until ch=#13;
   clearscreen;
end;


procedure resetgame;
begin
   score := 0;
   miss := 5;
   tx := 150;
   level := 1;
   burgeron := 0;
   hotdog := 0;
   td := 0;
   burg := 0;
   shoot := 0;
   for i := 1 to 10 do
      with sh2[i] do
         begin
            x := 0;
            y := 0;
            d := 0;
            shp := 0;
         end;
end;


procedure drawscore;
begin
   putletter(30,185,2,'score ');
   putletter(72,185,2,'      ');
   putletter(72,185,5,st(score));
end;


procedure drawmake;
begin
   for i:=1 to burgernum do putpic(sh1[0].shape,0,i*5+50);
   burgernum := bmake[level,0];
   for i := 1 to burgernum do
      with sh1[bmake[level,burgernum+1-i]] do
         putpic(shape,0,i*5+50);
   for i := 1 to 10 do
      begin
         if i/2=int(i/2)
            then
               begin
                  sound(1000);
                  delay(40);
                  nosound;
                  putletter(0,40,1,'make');
               end
            else putletter(0,40,2,'make');
         delay(200);
      end;
end;


procedure drawscreen;
begin
   clearscreen;
   draw(30,7,319,7,2); draw(319,7,319,182,2);
   draw(319,182,30,182,2); draw(30,182,30,7,2);
   for i := 179 to 180 do
      begin
         draw(52,i,tx-1,i,1);
         draw(tx+24,i,297,i,1);
      end;
   putletter(120,0,9,'burger blaster');
   putletter(170,185,2,'chances left ');
   putletter(261,185,5,st(miss));
   drawscore;
   putpic(sh1[8].shape,tx,181);
   getpic(sh1[10],tx,177,tx+35,181);
   putpic(sh1[6].shape,32,181);
   putpic(sh1[7].shape,298,181);
   putletter(0,195,3,'q');
   putletter(c,195,6,'uit  ');
   putletter(c,195,3,'Space');
   putletter(c,195,6,' fire  ');
   chaa := concat(#27,'     ',#26);
   putletter(c,195,3,chaa);
   putletter(c-35,195,4,'and');
   putletter(c+21,195,6,'move');
   putletter(c,195,3,'   return ');
   putletter(c,195,6,'stop');
   putletter(0,40,1,'make');
   burgernum:=1;
   drawmake;
   putletter(90,90,8,'press any key to start');
   repeat
      inkey;
   until ch<>#0;
   putletter(90,90,7,'                      ');
end;


procedure hotdoghit;
begin
   with sh2[i] do
      begin
         putpic(sh1[11].shape,x,y);
         putletter(x,y,7,'100');
         for c := 1 to 100 do
            begin
               sound(random(1000)+30);
               delay(random(3));
               nosound;
            end;
         hotdog := 0;
         shp := 0;
         score := score + 100;
         drawscore;
         putletter(x,y,7,'   ');
      end;
end;


procedure checkshot;
begin
   for i := 1 to 10 do
      with sh2[i] do
         if shp>0
            then
               begin
                 if (abs((x+10)-(sx+10))<15) and (abs(y-2-sy)<10) and (shoot=1)
                    then
                       begin
                          if shp=9
                             then hotdoghit
                             else
                                begin
                                   shp := -shp;
                                   for r := 1 to 400 do
                                   sound(random(1000)+30);
                                end;
                          shoot := 0;
                          nosound;
                       end;
               end;
end;


procedure shootgun;
begin
   draw(sx,sy,sx,sy-5,0);
   draw(sx+18,sy,sx+18,sy-5,0);
   sy := sy - 3;
   checkshot;
   if sy<12
      then shoot := 0
      else if shoot<>0
              then
                 begin
                    draw(sx,sy,sx,sy-5,1);
                    draw(sx+18,sy,sx+18,sy-5,1);
                 end;
end;


procedure drawburgers;
begin
   for i := 1 to burgeron do
      with sh1[bmake[level,i]] do
         putpic(shape,tx+8,179-((i-1)*5));
   getpic(sh1[10],tx,179-(i*5),tx+35,181);
end;


procedure movetray;
begin
   if ((ch=#27) and keypressed) or (td<>0)
      then
         begin
            if keypressed
               then read(kbd,ch);
            if ((ch='K') or (td=1)) and (tx>55)
               then
                  begin
                     td := 1;
                     tx := tx - 1;
                  end;
            if ((ch='M') or (td=-1)) and (tx<260)
               then
                  begin
                     td := -1;
                     tx := tx + 1;
                  end;
            putpic(sh1[10].shape,tx,181);
         end;
   if ch=#13
      then td := 0;
   if (ch=' ') and (shoot=0)
      then
         begin
            shoot := 1;
            sx := tx + 7;
            sy := 176 - (burgeron*5);
            td := 0;
            for i := 1000 downto 500 do
               sound(i);
            nosound;
         end;
   if shoot=1
      then shootgun;
end;


procedure nextround;
begin
   putletter(186,90,3,'                 ');
   putletter(100,90,3,'round completed');
   for i := 600 downto 100 do
      begin
         sound(i);
         delay(5);
      end;
   nosound;
   putletter(100,90,3,'               ');
   level := level + 1;
   for i := 1 to burgeron do
      putpic(sh1[0].shape,tx+8,179-((i-1)*5));
   putpic(sh1[8].shape,tx,181);
   getpic(sh1[10],tx,177,tx+35,181);
   burgeron := 0;
   if level>total
      then level := 1;
   drawmake;
end;


procedure correctland;
begin
   with sh2[burg] do
      begin
         if (abs(x+10-(tx+15))<10) and (bmake[level,burgeron+1]=abs(shp))
            then
               begin
                  putletter(x,y-14,7,'   ');
                  putletter(x,y-14,7,st(abs(d)*5));
                  sound(1000);
                  delay(20);
                  nosound;
                  burgeron := burgeron + 1;
                  score := score + (abs(d)*5);
                  drawscore;
                  drawburgers;
                  delay(200);
                  putletter(x,y-14,7,'   ');
                  if burgeron=burgernum
                     then nextround;
               end
            else
               begin
                  for i := 90 to 105 do
                     draw(100,i,226,i,0);
                  if bmake[level,burgeron+1]<>abs(shp)
                     then putletter(100,90,2,'   wrong piece    ')
                     else putletter(100,95,2,' missed the catch ');
                  sound(800);
                  delay(60);
                  nosound;
                  miss := miss - 1;
                  putletter(261,185,5,'  ');
                  putletter(261,185,5,st(miss));
                  delay(400);
                  if bmake[level,burgeron+1]<>abs(shp)
                     then putletter(100,90,2,'                  ')
                     else putletter(100,95,2,'                  ');
               end;
      end;
end;


procedure burgermove;
begin
   burg := burg + 1;
   if burg>10
      then burg := 1;
   with sh2[burg] do
      begin
         if (shp=0) and (random(100)<4)
            then
               begin
                  shp := random(6)+1;
                  if (shp=6) and (hotdog=1)
                     then shp := random(5)+1
                     else if shp=6
                             then
                                begin
                                   shp := 9;
                                   hotdog := 1;
                                end;
                  y := random(76)+33;
                  if shp=9
                     then d := random(8)+8
                     else d := random(15)+1;
                  x := 35;
                  if random<0.4
                     then
                        begin
                           d := -d;
                           x := 290;
                        end;
               end;
         if shp>0
            then
               begin
                  if shp=9
                     then putpic(sh1[11].shape,x,y)
                     else putpic(sh1[0].shape,x,y);
                  x := x + d;
                  if d<0
                     then x := x - abs(td*2)
                     else if d>0
                             then x := x + abs(td*2);
                  if (random(100)<4) and (shp=9)
                     then d := -d;
                  if (x<35) or (x>290)
                     then
                        begin
                           if shp=9
                              then hotdog := 0;
                           shp := 0;
                        end
                     else
                        begin
                           if shp=9
                             then putpic(sh1[9].shape,x,y)
                             else putpic(sh1[shp].shape,x,y);
                        end;
               end
            else if shp<0
                   then
                      begin
                         putpic(sh1[0].shape,x,y);
                         y := y + random(3)+2;
                         if y>176-(burgeron*5)
                            then
                               begin
                                  correctland;
                                  shp := 0;
                               end
                            else putpic(sh1[abs(shp)].shape,x,y);
                      end;
      end;
end;


begin
   getshapes;
   titlescreen;
   repeat
      resetgame;
      drawscreen;
      repeat
         inkey;
         movetray;
         burgermove;
      until miss=0;
      putletter(124,90,2,' game over ');
      putletter(90,97,3, 'press space to play again');
      putletter(93,104,3,'or any other key to quit');
      repeat
         if keypressed
            then read(kbd,ch);    { °±²Û clear keyboard buffer Û²±° }
      until not keypressed;
      repeat
         inkey;
      until ch<>#0;
   until ch<>' ';
   textmode(c80);
   textcolor(7);
   clrscr;
end.